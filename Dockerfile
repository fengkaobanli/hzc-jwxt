FROM python:3.13-slim

ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

# ddddocr 需要 libGL；换国内源加速（海外用户可改回默认源）
RUN sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/*.sources 2>/dev/null; \
    apt-get update \
 && apt-get install -y --no-install-recommends tzdata libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists

WORKDIR /app

COPY requirements.txt .
RUN pip install -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple -r requirements.txt

COPY *.py ./
COPY web ./web

EXPOSE 8770

CMD ["python", "/app/webapp.py", "--host", "0.0.0.0", "--port", "8770"]
