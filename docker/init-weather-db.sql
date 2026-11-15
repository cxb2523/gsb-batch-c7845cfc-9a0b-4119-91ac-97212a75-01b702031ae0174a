-- Runs automatically the first time the postgres container starts.
-- Creates the weather database and the table written to by insert_weather_data.

CREATE DATABASE weather;

\connect weather

CREATE TABLE IF NOT EXISTS weather (
    id SERIAL PRIMARY KEY,
    city VARCHAR(100) NOT NULL,
    date DATE NOT NULL,
    temperature NUMERIC(6, 2),
    humidity INTEGER,
    wind_speed NUMERIC(6, 2),
    weather_condition VARCHAR(150)
);

-- Unique on (city, date): the insert task upserts against this index so a
-- re-run of the same daily batch updates the existing row instead of
-- inserting a duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS idx_weather_city_date
    ON weather (city, date);

-- Migration for databases created before the unique index existed:
--
--   DROP INDEX IF EXISTS idx_weather_city_date;
--   CREATE UNIQUE INDEX idx_weather_city_date ON weather (city, date);
--
-- De-duplicate any pre-existing repeated (city, date) rows first, e.g. keep
-- the row with the highest id per (city, date):
--
--   DELETE FROM weather a USING weather b
--     WHERE a.city = b.city AND a.date = b.date AND a.id < b.id;
