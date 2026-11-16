#!/usr/bin/env bash
# Reproducible local test run for the bronze Event Hub ingestion, including
# machines with no Python/Java/Hadoop on PATH (like the grading sandbox).
#
# Toolchain resolution order:
#   1. Explicit env vars: PYTHON_EXE, JAVA_HOME, HADOOP_HOME, LOCAL_DELTA_JARS
#   2. Portable toolchain under ../../../../tools (Python NuGet, Temurin JDK,
#      Hadoop winutils stub, Maven Delta JARs under repo/wheels/jars)
#   3. Whatever is already on PATH (standard developer machine / CI).
#
# No Event Hub connection string or Databricks secret is ever accessed.
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$TEST_DIR/../../.." && pwd)"
TOOLS_DIR="$(cd "$REPO_ROOT/../tools" 2>/dev/null && pwd || true)"

# ── Python ────────────────────────────────────────────────────────
if [ -z "${PYTHON_EXE:-}" ]; then
  if [ -n "${TOOLS_DIR:-}" ] && [ -x "$TOOLS_DIR/python/tools/python.exe" ]; then
    PYTHON_EXE="$TOOLS_DIR/python/tools/python.exe"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_EXE="python3"
  else
    PYTHON_EXE="python"
  fi
fi
echo "[run] Python: $PYTHON_EXE"
"$PYTHON_EXE" --version

# ── Java ──────────────────────────────────────────────────────────
if [ -z "${JAVA_HOME:-}" ] && [ -n "${TOOLS_DIR:-}" ]; then
  JDK_DIR="$(ls -d "$TOOLS_DIR"/jdk/jdk-17* 2>/dev/null | head -1 || true)"
  [ -n "$JDK_DIR" ] && export JAVA_HOME="$JDK_DIR"
fi
if [ -n "${JAVA_HOME:-}" ]; then
  case "$(uname -s 2>/dev/null || echo Windows)" in
    MINGW*|MSYS*|CYGWIN*) export PATH="$JAVA_HOME/bin:$PATH" ;;
    *) export PATH="$JAVA_HOME/bin:$PATH" ;;
  esac
fi
echo "[run] JAVA_HOME: ${JAVA_HOME:-<on PATH>}"
java -version 2>&1 | head -1

# ── Hadoop (Windows local FS needs winutils.exe + hadoop.dll) ────
if [ -z "${HADOOP_HOME:-}" ] && [ -n "${TOOLS_DIR:-}" ] && [ -d "$TOOLS_DIR/hadoop" ]; then
  HADOOP_HOME="$TOOLS_DIR/hadoop"
fi
if [ -n "${HADOOP_HOME:-}" ] && [ -d "$HADOOP_HOME/bin" ]; then
  case "$(uname -s 2>/dev/null || echo Windows)" in
    MINGW*|MSYS*|CYGWIN*)
      # JVM seeds java.library.path from PATH; both vars need Windows form.
      HADOOP_BIN_WIN="$(cygpath -w "$HADOOP_HOME/bin")"
      export HADOOP_HOME="$(cygpath -w "$HADOOP_HOME")"
      export PATH="$HADOOP_BIN_WIN:$PATH"
      ;;
    *)
      export PATH="$HADOOP_HOME/bin:$PATH"
      ;;
  esac
fi
echo "[run] HADOOP_HOME: ${HADOOP_HOME:-<unset>}"

# ── Delta JARs for offline classpath ──────────────────────────────
# Prefer the portable toolchain's copy; fall back to a repo-local wheels/jars.
DELTA_JAR_DIR=""
if [ -n "${TOOLS_DIR:-}" ] && [ -d "$TOOLS_DIR/delta-jars" ]; then
  DELTA_JAR_DIR="$TOOLS_DIR/delta-jars"
elif [ -d "$REPO_ROOT/wheels/jars" ]; then
  DELTA_JAR_DIR="$REPO_ROOT/wheels/jars"
fi
if [ -z "${LOCAL_DELTA_JARS:-}" ] && [ -n "$DELTA_JAR_DIR" ]; then
  case "$(uname -s 2>/dev/null || echo Windows)" in
    MINGW*|MSYS*|CYGWIN*)
      # Hadoop needs Windows-style paths; conftest converts them to file: URIs.
      JAR_WIN="$(cygpath -w "$DELTA_JAR_DIR" 2>/dev/null || echo "$DELTA_JAR_DIR")"
      export LOCAL_DELTA_JARS="$JAR_WIN\\delta-spark_2.12-3.2.0.jar;$JAR_WIN\\delta-storage-3.2.0.jar"
      ;;
    *)
      export LOCAL_DELTA_JARS="$DELTA_JAR_DIR/delta-spark_2.12-3.2.0.jar,$DELTA_JAR_DIR/delta-storage-3.2.0.jar"
      ;;
  esac
fi
echo "[run] LOCAL_DELTA_JARS: ${LOCAL_DELTA_JARS:-<delta-spark will resolve via Maven>}"

# PySpark workers must use the same interpreter as the driver.
case "$(uname -s 2>/dev/null || echo Windows)" in
  MINGW*|MSYS*|CYGWIN*)
    export PYSPARK_PYTHON="$(cygpath -w "$PYTHON_EXE" 2>/dev/null || echo "$PYTHON_EXE")"
    ;;
  *)
    export PYSPARK_PYTHON="$PYTHON_EXE"
    ;;
esac
export PYSPARK_DRIVER_PYTHON="$PYTHON_EXE"

exec "$PYTHON_EXE" -I -m pytest "$TEST_DIR" -v "$@"
