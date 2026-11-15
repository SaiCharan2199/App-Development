# Official Apache Airflow image + the libraries our pipeline needs.
# 3.3.2 is the latest well-tested release (3.4.0 was only days old when chosen).
FROM apache/airflow:3.3.2-python3.12

# requests and PyYAML already come with Airflow. Installing "apache-airflow==3.3.2"
# in the same command stops pip from upgrading or downgrading Airflow itself.
RUN pip install --no-cache-dir \
        "apache-airflow==3.3.2" \
        "psycopg[binary]==3.2.3" \
        "beautifulsoup4==4.12.3" \
        "python-dotenv==1.0.1"
