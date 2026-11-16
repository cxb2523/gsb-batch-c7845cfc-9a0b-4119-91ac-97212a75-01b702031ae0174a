# Twitter Data Pipeline using Airflow and AWS S3

This project is designed to extract data from Twitter using the Twitter API and load it into an AWS S3 bucket in CSV format. The pipeline is managed and orchestrated using Airflow.

🌟 For those who crave more, discover the **Advanced Twitter Data Pipeline** [right here](https://github.com/kishlayjeet/Zomato-Twitter-Sentiment-Analysis-Data-Pipeline). 🌟

## Architecture

![Architecture Image](https://imgur.com/mAjDOxl.png)

- [Twitter API](https://developer.twitter.com/en/docs/twitter-api)
- [Apache Airflow](https://airflow.apache.org)
- [Amazon S3](https://aws.amazon.com/s3/)

The Twitter API is used to collect data from Twitter. The data collected from the Twitter API is saved locally before being transferred to the landing bucket on AWS S3. The ETL jobs are written in Python and scheduled in Apache Airflow.

## Environment Setup

### Hardware Used

I used a local machine with the following specifications:

```bash
  Ubuntu OS
  4 vCores, 4 GiB Memory
  Storage: 32 GiB
```

### Prerequisites

- A Twitter developer account and API key
- An AWS account and S3 bucket set up
- Python 3
- Airflow
- tweepy
- pandas
- boto3

## Installation

1. Clone the repository

```bash
git clone https://github.com/kishlayjeet/Twitter-Data-Pipeline-using-Airflow-and-AWS-S3.git
```

2. Install the required packages

```bash
pip install -r requirements.txt
```

3. Create a `config.py` file in the root directory of the project and add the following variables:

```python
AccessKey = "[Your Twitter API key]"
AccessSecret = "[Your Twitter API secret key]"
BearerToken = "[Your Twitter API bearer token]"
ConsumerKey = "[Your Twitter access token]"
ConsumerSecret = "[Your Twitter access token secret]"

KEY = "[Your AWS access key]"
SECRET = "[Your AWS secret key]"
```

4. Update the `airflow.cfg` file to point to your `config.py` file and set the appropriate S3 bucket name.

## Usage

1. Start the Airflow webserver

```bash
airflow standalone
```

![Airflow Server](https://imgur.com/RM9bEZb.png)

2. Access Airflow through your browser using the default `8080` port.

3. In the Airflow UI, enable the `twitter_data_pipeline` DAG.

4. The pipeline will run on the schedule defined in the DAG and load the data into the specified S3 bucket in CSV format.

Example output:

| id         | user     | username  | text                                            | like_count | reply_count | retweet_count | created_at                |
| :--------- | :------- | :-------- | :---------------------------------------------- | :--------- | :---------- | :------------ | :------------------------ |
| 161716...  | elonmusk | Elon Musk | Je me souviens Pluto 🥹                          | 23794      | 4036        | 1802          | 2023-01-21 09:07:40+00:00 |
| 161714...  | elonmusk | Elon Musk | Twitter is the source of truth                  | 35278      | 2525        | 3756          | 2023-01-21 08:25:50+00:00 |

The `id` column holds the tweet id and is used to de-duplicate rows across runs.

## Incremental collection and data-loss protection

The ETL no longer fetches only the latest 100 tweets and overwrites the S3
object on every run. `run_twitter_etl` in `twitter_data_pipeline/twitter_etl.py`
now works incrementally:

1. **Read the existing CSV first.** `refined_tweets.csv` is downloaded from S3
   and used as the lower bound for the run. Legacy files without an `id`
   column (or with an old pandas index column) are migrated to the canonical
   schema automatically.
2. **Page through the API.** Each request returns at most 100 tweets; the
   client follows `next_token` until there are no more pages or the run cap
   (`max_tweets`, default 1000) is reached.
3. **Tolerate mid-pagination failures.** If a page fails, the tweets already
   fetched are still merged and written. The failure is captured on the
   returned `EtlResult` (`status="partial_failure"`, populated `errors` list)
   and logged instead of discarding the whole batch.
4. **De-duplicate by tweet id.** Tweets already stored in S3, as well as ids
   repeated within the freshly fetched batch, are skipped and counted in
   `duplicate_tweets`.
5. **Never overwrite with an empty result.** When the API returns no data or
   every fetched tweet is a duplicate, the upload is skipped
   (`status="skipped_empty"`) and the existing S3 object is left byte-for-byte
   untouched.
6. **Abort on real S3 read errors.** A missing object (`NoSuchKey`/404) simply
   means this is the first run. Any other `get_object` failure raises and
   aborts the task before any data is fetched or written, so empty data can
   never clobber history.
7. **Stable output.** The written CSV keeps the columns
   `id, user, username, text, like_count, reply_count, retweet_count,
   created_at`, is sorted by `created_at` ascending (with `id` as the
   tie-breaker) and is written without a pandas index column, so repeated
   runs produce stable file content.

The function returns an `EtlResult` dataclass (status, counters, page count
and per-page errors) which Airflow can expose through XCom and which is also
emitted through the logger for observability. Both the Twitter client and the
S3 client are injected as arguments — real tweepy/boto3 clients are built by
lazy factory helpers when the function is called with no arguments from the
DAG, which also keeps the module importable without tweepy or `config.py`.

## Running the tests offline

The test suite under `tests/` uses in-memory fake Twitter and S3 clients, so
it needs no network access, AWS credentials, Twitter token or tweepy:

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

It covers the first run (no S3 object), incremental appends, duplicate
tweets, empty API responses, mid-pagination failures, S3 read failures and
migration of legacy CSV files.

## Notes

- The pipeline is currently set to extract data from a specific Twitter handle. You can update the `run_twitter_etl` function in `dags/twitter_etl.py` to extract data from other sources.
- The pipeline is currently set to load the data into a specific S3 bucket. You can update the `bucket_name` variable in `dags/twitter_etl.py` to load the data into other S3 buckets or in a different format.
- The pipeline is currently scheduled to run every seven days. You can update the `schedule_interval` in the DAG to run more or less frequently.

## Extras

- You can also check the output dataframe for a specific task by using `xcom_pull` from the `task_instance`.
- You can also check the S3 CSV file using the S3 bucket URL.
- You can also set up Airflow on other cloud platforms like GCP and Azure.

## Error Handling and Troubleshooting

- Airflow provides a UI for monitoring the status of tasks and DAG runs. In case of task failure, the UI displays the error message and the traceback, which can be used to troubleshoot the issue.
- Airflow also provides the option to retry failed tasks a certain number of times before marking them as failures. This can be configured in the DAG definition.
- In the `run_twitter_etl` function in `dags/twitter_etl.py`, you can include try-except blocks to catch and handle any errors that may occur while extracting data from the Twitter API.
- In case of issues with the S3 bucket, such as access denied or invalid credentials, check if the `config.py` file contains the correct AWS access key and secret key, and that the S3 bucket name is correctly configured in the `twitter_etl.py` file.
- Logs for the pipeline can also be found in the `logs/` directory. These logs can be useful in troubleshooting issues with the pipeline.

## Conclusion

In conclusion, this project provides a detailed guide on how to build a data pipeline using Airflow to extract data from the Twitter API, process it using pandas, and load it into an AWS S3 bucket in CSV format. By following the step-by-step instructions in this README, you should be able to set up and run the pipeline successfully.

## Important Considerations

It is important to note that this project is intended for educational or testing purposes. Before using the data in production, it is crucial to comply with Twitter's terms of service and any other relevant laws and regulations. It is also important to consider data privacy, security, and compliance in any production implementation.

## Future Enhancements

This project provides a solid foundation that can be further enhanced by adding more functionalities such as data cleaning, transformation, and analysis. It can also be integrated with other systems and services, such as data warehousing, machine learning, and visualization tools, to enable more advanced use cases.

## Author

Thank you for using this data pipeline. If you have any questions, suggestions, or feedback, please don't hesitate to contact me at contact.kishlayjeet@gmail.com.
