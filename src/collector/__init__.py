"""What runs on every monitored machine: the collector, the read-only Docker proxy and the optional system helper.

Each is one standard-library file, built into its own small image (see docker/*.Dockerfile), so none of them
imports anything from the backend or from each other."""
