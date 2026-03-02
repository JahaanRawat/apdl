# Stage 1: Build
FROM ubuntu:22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    python3 \
    python3-pip \
    git \
    pkg-config \
    libssl-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install conan==1.64.1

WORKDIR /build

# Copy dependency files first for layer caching
COPY conanfile.txt .
RUN conan profile new default --detect && \
    conan profile update settings.compiler.libcxx=libstdc++11 default && \
    conan install . --build=missing -s build_type=Release

# Copy source
COPY CMakeLists.txt .
COPY include/ include/
COPY src/ src/

# Build
RUN mkdir -p build && cd build && \
    cmake .. \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PREFIX_PATH=/build \
    && cmake --build . --target config_service -j$(nproc)

# Stage 2: Minimal runtime
FROM ubuntu:22.04 AS runtime

RUN apt-get update && apt-get install -y \
    libssl3 \
    libpq5 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r apdl && useradd -r -g apdl -d /app -s /sbin/nologin apdl

WORKDIR /app

COPY --from=builder /build/build/config_service .

RUN chown -R apdl:apdl /app

USER apdl

EXPOSE 8081

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8081/health || exit 1

CMD ["./config_service"]
