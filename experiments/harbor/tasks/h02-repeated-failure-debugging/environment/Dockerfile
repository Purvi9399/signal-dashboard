FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
# pytest from vendored, hash-pinned wheels: the build needs no network (scripts/fetch_wheels.sh)
COPY wheels/ /tmp/wheels/
COPY requirements-env.txt /tmp/requirements-env.txt
RUN pip install --no-cache-dir --no-index --find-links /tmp/wheels --require-hashes -r /tmp/requirements-env.txt \
 && rm -rf /tmp/wheels /tmp/requirements-env.txt \
 && useradd --create-home --uid 1000 agent \
 && mkdir /app && chown agent:agent /app
WORKDIR /app
COPY --chown=agent:agent app/ /app/
