
### Stage 1: Build Django application

FROM python:3.13-slim AS django-builder

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies for building Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libmariadb-dev-compat \
    libmariadb-dev \
    libssl-dev \
    default-libmysqlclient-dev \
    build-essential \
    pkg-config \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip && \
    pip install setuptools && \
    pip install wheel && \
    pip install -r requirements.txt

# Copy project
COPY . /app/

# Collect static files (if applicable)
RUN python manage.py collectstatic --noinput

### Stage 2: Nginx with environment variable substitution

FROM nginx:1.21-alpine AS nginx

# Install envsubst for environment variable substitution
RUN apk add --no-cache gettext

# Copy .env file
COPY .env /app/.env

# Copy custom Nginx configuration template
COPY nginx.conf.template /etc/nginx/conf.d/nginx.conf.template

# Substitute environment variables in nginx.conf.template
RUN envsubst < /etc/nginx/conf.d/nginx.conf.template > /etc/nginx/conf.d/default.conf

# Copy static files from Django builder
COPY --from=django-builder /app/static /app/static
COPY --from=django-builder /app/media /app/media

# Expose port 80
EXPOSE 80

### Stage 3: Final Django application with Gunicorn

FROM python:3.13-slim AS django

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies for running the application
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmariadb-dev-compat \
    libssl-dev \
    default-libmysqlclient-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies from builder
COPY --from=django-builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=django-builder /usr/local/bin/gunicorn /usr/local/bin/gunicorn

# Copy application code
COPY --from=django-builder /app /app

# Expose port 8000
EXPOSE 8000

# Command to run the application
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "roma.wsgi:application"]
