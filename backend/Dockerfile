# OpenArchive 앱 이미지 — API·임베딩 워커·웹 화면 (#95-e2, ADR-039 개정).
#
# DB는 넣지 않는다. 앱은 OpenSQL(또는 PostgreSQL 17 + pgvector)에 붙는 클라이언트라
# 컨테이너에 넣어도 실제 대상을 우회하지 않는다 (ROADMAP 「배포와 설치」).
#
#   docker build -t openarchive backend/
#   docker run -d -p 8000:8000 -v openarchive:/data \
#     -e DATABASE_URL=postgresql://… -e ADMIN_PASSWORD=… openarchive
#
# 웹 화면은 저장소에 커밋된 정적 빌드(openarchive/static)를 그대로 싣는다 — Node 단계가 없다.
FROM python:3.12-slim-bookworm

# 이미지·스캔 PDF 텍스트 인식(ADR-052). 한국어 모델이 빠지면 그 문서만 「텍스트 인식 실패」다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-kor \
    && rm -rf /var/lib/apt/lists/*

# CPU 전용 torch를 먼저 깐다. 이게 없으면 sentence-transformers가 기본 인덱스의 CUDA 빌드를
# 끌고 와 이미지가 수 GB 커진다 — GPU는 쓰지 않는다.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY . /src
RUN pip install --no-cache-dir "/src[local]" && rm -rf /src

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# 설정(.env)과 BGE-M3 가중치(약 2.3GB)를 한 볼륨에 둔다. 가중치는 이미지에 굽지 않고 첫
# 기동 때 받는다 — 컨테이너를 다시 만들어도 볼륨이 남아 있으면 다시 받지 않는다.
RUN useradd --create-home --uid 1000 openarchive \
    && mkdir /data && chown openarchive:openarchive /data
USER openarchive
ENV OPENARCHIVE_HOME=/data \
    HF_HOME=/data/huggingface \
    EMBEDDING_PROVIDER=local \
    PYTHONUNBUFFERED=1
VOLUME /data

EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
