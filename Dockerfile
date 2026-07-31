# Partimos de una imagen que YA trae Python + Chrome instalados y listos.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

# Carpeta de trabajo dentro del contenedor.
WORKDIR /app

# Copiamos primero solo requirements.txt para instalar librerías.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium

# Copiamos el resto de nuestro código.
COPY . .

# Puerto en el que va a escuchar nuestro servicio dentro del contenedor.
EXPOSE 8000

# Comando que arranca el servicio cuando el contenedor se enciende.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
