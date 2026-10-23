PROTO      := proto/ufmt/v1/worker.proto
PYTHON     ?= python3
GOBIN      := $(shell go env GOPATH)/bin

.PHONY: all proto build test clean

all: build

proto:
	mkdir -p ai/gen/ufmtv1 ocr/gen browser/src/gen ufmt_monitor/gen
	protoc -I proto --plugin=protoc-gen-go=$(GOBIN)/protoc-gen-go \
		--go_out=ai/gen/ufmtv1 --go_opt=paths=source_relative $(PROTO)
	mv ai/gen/ufmtv1/ufmt/v1/worker.pb.go ai/gen/ufmtv1/ && rm -rf ai/gen/ufmtv1/ufmt
	protoc -I proto --c_out=ocr/gen $(PROTO)
	protoc -I proto --plugin=protoc-gen-es=browser/node_modules/.bin/protoc-gen-es \
		--es_out=browser/src/gen --es_opt=target=js $(PROTO)
	$(PYTHON) -m grpc_tools.protoc -I proto --python_out=ufmt_monitor/gen $(PROTO)

build:
	$(MAKE) -C ocr
	cd ai && go build -o bin/ufmt-ai ./cmd/ufmt-ai

test: build
	$(MAKE) -C ocr test
	cd ai && go vet ./... && go test ./...
	cd browser && npm test
	$(PYTHON) -m pytest

clean:
	$(MAKE) -C ocr clean
	rm -rf ai/bin
