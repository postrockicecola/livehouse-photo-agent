#!/bin/bash

# 定义路径
PROTO_DIR="./api/proto"
GEN_PY_DIR="./api/gen/python"
GEN_GO_DIR="./api/gen/go"

# 确保输出目录存在
mkdir -p $GEN_PY_DIR $GEN_GO_DIR

echo "--- 🔨 开始生成 gRPC 代码 ---"

# 1. 生成 Python 代码
python3 -m grpc_tools.protoc \
    -I$PROTO_DIR \
    --python_out=$GEN_PY_DIR \
    --grpc_python_out=$GEN_PY_DIR \
    $PROTO_DIR/vision.proto

# 2. 生成 Go 代码 (如果以后需要 Go 侧调用)
# protoc -I$PROTO_DIR --go_out=$GEN_GO_DIR --go-grpc_out=$GEN_GO_DIR $PROTO_DIR/vision.proto

# 3. 修复 Python 导入路径问题（gRPC 默认生成的导入在深层目录下有时会报错）
touch $GEN_PY_DIR/__init__.py

echo "--- ✅ 生成完成！代码已存入 api/gen/ ---"