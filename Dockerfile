FROM python:3.11-slim

WORKDIR /app

ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 安装基础依赖
RUN apt-get update && apt-get install -y \
    iputils-ping \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 设置时区
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 升级 pip 并安装依赖
COPY app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY . .

EXPOSE 8080

CMD ["python", "app/main.py"]
