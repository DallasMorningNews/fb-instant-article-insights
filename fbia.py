import sys
import os
from datetime import date

import requests
import xmltodict
import dataset
from dotenv import load_dotenv, find_dotenv
import slacker


# Get config from .env file
load_dotenv(find_dotenv())


def get_insights_total(url, metric, page_access_token):
    """
    Get a metric from the Facebook Instant Articles Insights API for the
    passed URL. The function will try to get all data available, going back to
    the earliest date available for the given metric and totaling them before
    returning them. Uses:
    https://developers.facebook.com/docs/graph-api/reference/v2.6/instant-article-insights
    """
    # Different metrics are available on different increments, so adapt ...
    period = 'week'
    if metric == 'all_views':
        period = 'day'

    # ... same goes for the first available date
    since = '15 Jan, 2016'
    if metric == 'all_view_durations_average':
        since = '24 Mar, 2016'

    query = 'instant_article{insights.metric(%s).period(%s).since(%s)}' % (
        metric, period, since,)

    api_request_params = {
        'fields': query,
        'id': url,
        'access_token': page_access_token,
    }
    api_request = requests.get('https://graph.facebook.com/v2.6/',
                               params=api_request_params)
    if api_request.status_code != 200:
        print('HTTP error fetching "%s" from Facebook API.' % api_request.url)
        sys.exit(1)

    try:
        views = api_request.json()['instant_article']['insights']['data']
    except KeyError:
        # Sometimes data aren't available for an article. In those cases, we
        # just return 0
        print('** No data for "%s" metric from "%s".' % (
            metric, api_request.url,))
        return 0

    metric_total = 0
    for day in views:
        metric_total = metric_total + int(day['value'])

    return metric_total


def get_long_lived_user_token():
    """
    On first run, the script should be passed a user token with the
    read_insights permission for a user that has manage_page rights on the
    page that owns the articles. We'll exchange that token here for a long-
    lived access token, which will allow us to request a page token that
    never expires. Details:
    https://developers.facebook.com/docs/facebook-login/access-tokens/expiration-and-extension
    """
    print('- Exchanging the provided user token for a long-lived token')
    token_api_url = ('https://graph.facebook.com/oauth/access_token')
    token_request_params = {
        'grant_type': 'fb_exchange_token',
        'client_id': os.environ.get('FB_CLIENT_ID'),
        'client_secret': os.environ.get('FB_CLIENT_SECRET'),
        'fb_exchange_token': os.environ.get('FB_USER_TOKEN'),
    }
    token_request = requests.get(token_api_url, params=token_request_params)

    try:
        return token_request.text.split('=')[1].split('&')[0]
    except IndexError:
        print('Failed to fetch a page access token. Response: "%s" from "%s"' %
              (token_request.text, token_request.url,))
        sys.exit(1)


def get_page_access_token():
    """
    Get a page access token, but cache it in our SQLite database. We save it
    locally because eventually the user token will expire, but the page token
    won't so we don't want to keep refreshing. Also, we call this function
    frequently and there's no need to roundtrip the API to refresh the token
    everytime. Details:
    https://developers.facebook.com/docs/pages/access-tokens
    """
    print('- Getting a Facebook page access token')
    fb_table = db['credentials']
    token_row = fb_table.find_one(type='page_token')

    if token_row is not None:
        return token_row['token']

    print('- Fetching a new Facebook page access token')
    token_api_url = ('https://graph.facebook.com/v2.6/dallasmorningnews/')
    token_request_params = {
        'fields': 'access_token',
        'access_token': get_long_lived_user_token(),
    }
    token_request = requests.get(token_api_url, params=token_request_params)

    try:
        page_token = token_request.json()['access_token']
        fb_table.insert({'type': 'page_token', 'token': page_token})
        return page_token
    except KeyError:
        print('Failed to fetch a page access token. Response: "%s"' %
              token_request.text)
        sys.exit(1)


def get_facebook_feed():
    """
    Fetch the feed that Facebook uses to populate the Instant Articles feed.
    It'll be our starting point for fetching insights data because we need
    canonical article URLs to build our queries.
    """
    feed_url = os.environ.get('FEED_URL')
    print('- Getting article URLs from RSS feed "%s"' % feed_url)
    feed = requests.get(feed_url)
    return xmltodict.parse(feed.text)


def post_insights_to_slack(to_upload='fbia.csv', channels=('C0KF7RARL',)):
    """
    Post the findings to Slack. By default it grabs the fbia.csv file, which
    is created by a dataset.freeze operation after the API queries have
    finished.
    """
    today = date.today().strftime('%b %d, %Y')

    print('- Pushing insights to Slack')
    try:
        slack.files.upload(
            to_upload,
            channels=channels,
            filetype='csv',
            title='Facebook Insights report for %s' % today,
            initial_comment='Here are the latest :chart_with_upwards_trend: numbers \
    for our Facebook Instant Articles.',
        )
    except slacker.Error as e:
        print('Error posting to the Slack API: "%s"' % e)
        sys.exit(1)


def get_insights(parsed_feed):
    """
    Take the parsed RSS feed and try to fetch insights data for each entry,
    running separate queries for each insights metric available. Then upsert
    into our SQLite database so we still have the data after the stories roll
    off the RSS feed.
    """
    print('- Fetching insights from Facebook API')
    insights_table = db['fbia']
    for feed_item in parsed_feed['rss']['channel']['item']:
        print(' -- Getting data for instant article "%s"' % feed_item['title'])

        item_url = feed_item['link']

        row = {
            'id': feed_item['guid'],
            'Headline': feed_item['title'],
            'Publication date': feed_item['pubDate'],
            'Author': feed_item['author'],
            'URL': feed_item['link'],
            'Total views': get_insights_total(
                item_url, 'all_views', page_access_token),
            'Average view duration': get_insights_total(
                item_url, 'all_view_durations_average', page_access_token),
            'Average scroll depth': get_insights_total(
                item_url, 'all_scrolls_average', page_access_token),
        }

        insights_table.upsert(row, ['id'])

    return insights_table


if __name__ == '__main__':
    # Instantiate SQLite database, slack API
    db = dataset.connect('sqlite:///fbia.sqlite')
    slack = slacker.Slacker(os.environ.get('SLACK_API_KEY'))

    # Authenticate with FBAPI
    page_access_token = get_page_access_token()

    # Get the RSS feed from the CMS
    parsed_feed = get_facebook_feed()

    # Get the insights data from the Facebook API
    insights_table = get_insights(parsed_feed)

    # Freeze the data to CSV and upload it to Slack
    print('- Writing results to CSV')
    insights = insights_table.all()
    dataset.freeze(insights, format='csv', filename='fbia.csv')
    post_insights_to_slack()

    print('Done!')
