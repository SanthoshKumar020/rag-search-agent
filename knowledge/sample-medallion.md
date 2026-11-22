# Medallion Architecture — Sample Notes

The medallion architecture is a data design pattern used in lakehouse
platforms such as Databricks with Delta Lake. It organises data into
three quality layers: Bronze, Silver and Gold.

## Bronze layer

The Bronze layer stores raw data exactly as it arrives from source
systems. No transformations are applied beyond basic metadata such as
ingestion timestamps and source file names. Bronze tables preserve the
full history of the data so any downstream table can be rebuilt.

## Silver layer

The Silver layer contains cleaned, deduplicated and validated data.
Schemas are enforced, nulls are handled, and records from multiple
sources are conformed into consistent tables. Silver is the layer most
data engineers query when building features and reports.

## Gold layer

The Gold layer holds business-ready, aggregated data modelled around
consumption use cases — dashboards, KPIs and machine-learning features.
Gold tables are typically organised by subject area, for example daily
sales by region or monthly active users.

## Why teams use it

Each layer improves data quality step by step, so problems can be
traced back to the layer where they were introduced. Delta Lake adds
ACID transactions, schema enforcement and time travel on every layer,
which makes the pattern reliable in production pipelines built with
Apache Spark and PySpark.
