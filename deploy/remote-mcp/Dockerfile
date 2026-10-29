FROM python:3.12-slim-bookworm
# Optional build_ca is an operator-provided CA bundle for verified proxy TLS.
RUN --mount=type=secret,id=build_ca,target=/run/secrets/build_ca,mode=0444 \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources && \
    if [ -f /run/secrets/build_ca ]; then printf 'Acquire::https::CaInfo "/run/secrets/build_ca";\n' > /tmp/apt-tls.conf; else touch /tmp/apt-tls.conf; fi && \
    apt-get -c /tmp/apt-tls.conf update && apt-get -c /tmp/apt-tls.conf install -y --no-install-recommends ca-certificates libfontconfig1 libgl1 libx11-6 libxcursor1 libxi6 libxinerama1 libxrandr2 libxrender1 && \
    rm -rf /var/lib/apt/lists/* /tmp/apt-tls.conf
WORKDIR /opt/arrow
COPY services/story_service/requirements.lock services/story_service/requirements.lock
COPY services/story_mcp/requirements.lock services/story_mcp/requirements.lock
RUN --mount=type=secret,id=build_ca,target=/run/secrets/build_ca,mode=0444 \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install --no-cache-dir -r services/story_mcp/requirements.lock
RUN --mount=type=secret,id=build_ca,target=/run/secrets/build_ca,mode=0444 python - <<'PY'
import hashlib,io,ssl,urllib.request,zipfile
from pathlib import Path
url='https://github.com/godotengine/godot-builds/releases/download/4.6.3-stable/Godot_v4.6.3-stable_linux.x86_64.zip'
expected='a035258da32b77f966a5376f9fa29c30a6adde826a85ba918e1605bd1fc9823eba7d85f1dd5e748956bd2ba72827c0025ffa11bb82aec91128c407a2e723c99c'
ca=Path('/run/secrets/build_ca')
context=ssl.create_default_context(cafile=str(ca) if ca.exists() else None)
data=urllib.request.urlopen(url,timeout=120,context=context).read()
assert hashlib.sha512(data).hexdigest()==expected
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    Path('/usr/local/bin/godot').write_bytes(archive.read('Godot_v4.6.3-stable_linux.x86_64'))
Path('/usr/local/bin/godot').chmod(0o755)
PY
COPY services services
COPY addons/arrow_dialogic addons/arrow_dialogic
COPY docs docs
COPY license copyright ./
RUN chmod -R a+rX /opt/arrow && \
    useradd --uid 10001 --create-home arrow && mkdir /data && chown arrow:arrow /data
USER arrow
ENV ARROW_STORY_GODOT=/usr/local/bin/godot ARROW_STORY_DB=/data/stories.sqlite3
CMD ["python", "-m", "services.story_service", "serve", "--host", "0.0.0.0"]
