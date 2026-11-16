from src import DuckDuckGo

queries = [
  "Mango",
  "Watermelon",
]

results = DuckDuckGo.search(queries, max=10)

# `results` is still the flat list of every scraped item, and it also carries
# the per-query settlement for this batch:
#   results.summary["successful_count"] / ["successful"] -> fully scraped queries
#   results.summary["partial_count"] / ["partial"]       -> kept results, pagination failed
#   results.summary["failed_count"] / ["failed"]         -> queries with no data + error type
print("Total results:", len(results))
print("Summary:", results.summary)
