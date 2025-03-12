# Stage 1: Build stage
FROM python:3.13-slim AS builder

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install necessary system dependencies to build mysqlclient
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    libmariadb-dev \
    python3-dev \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Ensure symbolic links are created
RUN ln -sf /usr/lib/x86_64-linux-gnu/libmariadb.so.3 /usr/lib/libmariadb.so.3 || true

# Update the dynamic linker cache
RUN ldconfig

# Upgrade pip, setuptools, and wheel to avoid build issues
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy requirements file
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2: Production stage
FROM python:3.13-slim

# Install runtime dependencies for mysqlclient
RUN apt-get update && apt-get install -y \
    libmariadb3 \
    && rm -rf /var/lib/apt/lists/*

# Ensure symbolic links are created
RUN ln -sf /usr/lib/x86_64-linux-gnu/libmariadb.so.3 /usr/lib/libmariadb.so.3 || true

# Update the dynamic linker cache
RUN ldconfig

RUN useradd -m -r appuser && \
    mkdir /app && \
    chown -R appuser /app

# Copy project files
COPY --from=builder /usr/local/lib/python3.13/site-packages/ /usr/local/lib/python3.13/site-packages/
COPY --from=builder /usr/local/bin /usr/local/bin

# Set working directory
WORKDIR /app

# Copy project files
COPY --chown=appuser:appuser . . 

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set user
USER appuser

# Expose the port Django runs on
EXPOSE 8000

# Run the Django server
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "roma.wsgi:application"]
