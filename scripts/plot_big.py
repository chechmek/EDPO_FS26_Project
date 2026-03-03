import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("results.csv")

# Throughput vs Producers
for acks in df["acks"].unique():
    subset = df[df["acks"] == acks]

    plt.plot(
        subset["producers"],
        subset["throughput(msg/sec)"],
        label=f"acks={acks}"
    )

plt.xlabel("Number of Producers")
plt.ylabel("Throughput")
plt.legend()
plt.title("Producer Scaling")
plt.show()