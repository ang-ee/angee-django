# Network is used only while an administrator prepares this trusted image.
ARG BASE_IMAGE=python@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1
FROM ${BASE_IMAGE}
COPY requirements.txt /prepared/requirements.txt
RUN python -m pip download --no-cache-dir --require-hashes -r /prepared/requirements.txt -d /wheelhouse \
 && python -m venv /opt/catalogue \
 && /opt/catalogue/bin/python -m pip install --no-cache-dir --no-index --find-links=/wheelhouse --require-hashes -r /prepared/requirements.txt
ENV PATH="/opt/catalogue/bin:/usr/local/bin:/usr/bin:/bin"
USER 65532:65532
ENTRYPOINT ["/opt/catalogue/bin/python"]
CMD ["-I", "/work/artifact", "/work/repository"]
