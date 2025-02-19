# Use official Python image
FROM python:3.13

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set working directory
WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y default-mysql-client
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose the port Django runs on
EXPOSE 8000

# Run the Django server
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "roma.wsgi:application"]
