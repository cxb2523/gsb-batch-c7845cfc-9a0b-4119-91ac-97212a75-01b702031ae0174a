import os
import sys

# Make ``import twitter_etl`` work without installing the package or relying
# on Airflow's sys.path configuration.
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), '..', 'twitter_data_pipeline'),
)
