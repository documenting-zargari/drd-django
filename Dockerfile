FROM python:3.11-bullseye

# Install system dependencies
RUN apt-get update && apt-get install -y \
    default-libmysqlclient-dev \
    nginx \
    vim \
    build-essential \
    pkg-config \
    curl \
    gnupg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install ArangoDB client tools (arangodump, arangorestore)
RUN curl -fsSL https://download.arangodb.com/arangodb312/DEBIAN/Release.key | gpg --dearmor -o /usr/share/keyrings/arangodb.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/arangodb.gpg] https://download.arangodb.com/arangodb312/DEBIAN/ /" > /etc/apt/sources.list.d/arangodb.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends arangodb3-client \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
RUN ln -sf /dev/stdout /var/log/nginx/access.log \
    && ln -sf /dev/stderr /var/log/nginx/error.log

COPY nginx.default /etc/nginx/sites-available/default


# copy source and install dependencies

RUN mkdir -p /opt/app
COPY requirements.txt start-server.sh /opt/app/
RUN pip install -U pip \
    && pip install -r /opt/app/requirements.txt --no-cache-dir \
    && pip install gunicorn --no-cache-dir
COPY . /opt/app
WORKDIR /opt/app
RUN chown -R www-data:www-data /opt/app && \
    chmod +x /opt/app/start-server.sh

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# start server
EXPOSE 80
STOPSIGNAL SIGTERM
CMD ["/opt/app/start-server.sh"]