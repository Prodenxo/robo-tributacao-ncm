FROM python:3.12-slim

# Instalar dependências do sistema para o Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg2 \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libxshmfence1 fonts-liberation libappindicator3-1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalar o Chromium do Playwright
RUN playwright install chromium

# Copiar código da aplicação
COPY . .

# Porta padrão
EXPOSE 8000

# Rodar o servidor
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
