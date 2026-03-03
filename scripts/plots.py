import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

sns.set(style="whitegrid")

df = pd.read_csv("results.csv")

# -------------------------
# 1. Batch Size Impact
# -------------------------
plt.figure()
sns.lineplot(
    data=df,
    x="batch_size",
    y="throughput(msg/sec)",
    hue="acks",
    marker="o"
)
plt.title("Effect of Batch Size on Throughput")
plt.savefig("fig_batch_throughput.png")
plt.close()

plt.figure()
sns.lineplot(
    data=df,
    x="batch_size",
    y="avg_latency(sec)",
    hue="acks",
    marker="o"
)
plt.title("Effect of Batch Size on Latency")
plt.savefig("fig_batch_latency.png")
plt.close()

# -------------------------
# 2. ACKS Impact
# -------------------------
plt.figure()
sns.barplot(
    data=df,
    x="acks",
    y="throughput(msg/sec)"
)
plt.title("Impact of Acknowledgment Level on Throughput")
plt.savefig("fig_acks_throughput.png")
plt.close()

# -------------------------
# 3. Partition Scaling
# -------------------------
plt.figure()
sns.lineplot(
    data=df,
    x="partitions",
    y="throughput(msg/sec)",
    marker="o"
)
plt.title("Throughput Scaling with Partition Count")
plt.savefig("fig_partition_scaling.png")
plt.close()

# -------------------------
# 4. Producer Scaling
# -------------------------
plt.figure()
sns.lineplot(
    data=df,
    x="producers",
    y="throughput(msg/sec)",
    marker="o"
)
plt.title("Throughput Scaling with Number of Producers")
plt.savefig("fig_producer_scaling.png")
plt.close()

print("All figures generated.")