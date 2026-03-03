from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError
import time

KAFKA_BOOTSTRAP = "localhost:9092"


def recreate_topic(topic_name, partitions, replication=1):

    admin = KafkaAdminClient(
        bootstrap_servers=KAFKA_BOOTSTRAP
    )

    # Delete topic if exists
    try:
        admin.delete_topics([topic_name])
        time.sleep(3)
    except Exception:
        pass

    # Create topic
    topic = NewTopic(
        name=topic_name,
        num_partitions=partitions,
        replication_factor=replication
    )

    try:
        admin.create_topics([topic])
        print(f"Created topic {topic_name} with {partitions} partitions")
    except TopicAlreadyExistsError:
        print("Topic already exists")

    admin.close()