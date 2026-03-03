import multiprocessing as mp
import csv
import time

from producer_worker import run_producer
from experiment_monitor import monitor_system
from topic_manager import recreate_topic
from config import *

# -----------------------------
# Experiment Parameters
# -----------------------------
PARTITION_OPTIONS = [1, 3, 6]

RESULT_FILE = "results.csv"


# -----------------------------
# Run Load Test
# -----------------------------
def run_load(topic, batch, acks, producer_count):

    print(
        f"\nRunning -> "
        f"topic={topic}, "
        f"batch={batch}, "
        f"acks={acks}, "
        f"producers={producer_count}"
    )

    start_time = time.time()

    pool = mp.Pool(producer_count)

    results = pool.starmap(
        run_producer,
        [(topic, batch, acks)] * producer_count
    )

    pool.close()
    pool.join()

    duration = time.time() - start_time

    # Aggregate producer results
    total_throughput = sum(r["throughput"] for r in results)
    avg_latency = sum(r["avg_latency"] for r in results) / len(results)

    # System monitoring
    cpu, memory = monitor_system(duration=5)

    return total_throughput, avg_latency, cpu, memory, duration


# -----------------------------
# Main Experiment Loop
# -----------------------------
def main():

    with open(RESULT_FILE, "w", newline="") as file:

        writer = csv.writer(file)

        writer.writerow([
            "partitions",
            "batch_size",
            "acks",
            "producers",
            "throughput(msg/sec)",
            "avg_latency(sec)",
            "cpu(%)",
            "memory(%)",
            "duration(sec)"
        ])

        # -------------------------
        # Partition Experiment Loop
        # -------------------------
        for partitions in PARTITION_OPTIONS:

            topic_name = f"experiment-p{partitions}"

            print(f"\nCreating topic: {topic_name}")
            recreate_topic(topic_name, partitions)

            time.sleep(5)  # allow broker stabilization

            for batch in BATCH_SIZES:
                for acks in ACKS_OPTIONS:
                    for producers in PRODUCER_COUNTS:

                        throughput, latency, cpu, mem, duration = run_load(
                            topic_name,
                            batch,
                            acks,
                            producers
                        )

                        writer.writerow([
                            partitions,
                            batch,
                            acks,
                            producers,
                            throughput,
                            latency,
                            cpu,
                            mem,
                            duration
                        ])

                        file.flush()

    print("\n✅ All experiments completed.")
    print(f"Results saved to {RESULT_FILE}")


# -----------------------------
if __name__ == "__main__":
    main()