FROM python:3.14-slim@sha256:d3400aa122fa42cf0af0dbe8ec3091b047eac5c8f7e3539f7135e86d855dc015
ARG APDL_BACKEND_VERSION
ARG APDL_BUILD_REVISION
RUN python -c 'import os,re; assert re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?", os.environ["APDL_BACKEND_VERSION"]); assert re.fullmatch(r"[0-9a-f]{40}", os.environ["APDL_BUILD_REVISION"])'
ENV APDL_BACKEND_VERSION=${APDL_BACKEND_VERSION} \
    APDL_BUILD_REVISION=${APDL_BUILD_REVISION}
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY app/ app/
COPY scripts/create_admin_user.py scripts/create_admin_user.py
COPY scripts/assign_project_owner.py scripts/assign_project_owner.py
EXPOSE 8085
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8085", "--no-proxy-headers"]
