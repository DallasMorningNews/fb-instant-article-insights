# fb-instant-article-insights

The Python script in this repo uses the Facebook Insights API to grab views, view duration and scroll depth for our latest Facebook Instant Articles. Specifically it:

1. Goes out to the RSS feed we feed to Facebook to get a list of articles to query with
2. Uses the Insights API to get numbers for each article
3. Saves it all to a SQLite database (because eventually stories roll off the RSS feed)
4. Serializes the database to a CSV file and uploads it to Slack

## Installation

1. `pip install -r requirements.txt`
2. Copy the [`.env.example`](.env.example) file and fill in the Facebook API info and Slack API key. You'll need to create a Facebook app to get the credentials.

## Usage

You'll need a user token from a user with admin access to the page that owns the articles. And you'll need to create a token for that user, using your app, that has the `read_insights` permission. The first time you run the script, pass it that token:

```
$ FB_USER_TOKEN=your-token python fbia.py
```

Now there's a permanent page access token stored in the SQLite database and you won't need to generate a user token as long as that database is around and the user has admin access. You can safely set the script to run using `cron` or some other scheduler.

## Copyright

&copy; 2016 The Dallas Morning News
