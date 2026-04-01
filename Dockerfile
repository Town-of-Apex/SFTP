# Use a modern Python image with UV pre-installed
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# Set working directory
WORKDIR /app

# Install system dependencies (specifically tzdata for timezone management)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Set time zone to Eastern Time
ENV TZ=America/New_York
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy project configuration files
COPY pyproject.toml uv.lock ./

# Install the project's dependencies
RUN uv sync --frozen --no-install-project

# Copy the source code
COPY main.py scheduler.py ./

# Note: Apex.key and CSV will be mounted as volumes for security and easy updates

# Command to start the scheduler
CMD ["uv", "run", "python", "scheduler.py"]
