"""Exercise configured LSP logging with local collectors for every OTLP transport."""
from pathlib import Path
import importlib
import logging
import os
import tempfile
from unittest.mock import patch

from basilisp.main import init
from basilisp.lang.keyword import keyword as kw
from basilisp.lang.map import map as lmap

init()
module = importlib.import_module("basilisp_tools.lsp")

def main():
    def conv(value):
        if isinstance(value, dict):
            return lmap({(key if key.startswith("otel.") else kw(key)): conv(item) for key, item in value.items()})
        return value
    start = module.start_workspace_logging__BANG__
    stop = module.stop_workspace_logging__BANG__
    with tempfile.TemporaryDirectory() as root:
        state = start(root, conv({"log-path": "nested/log.txt"}), conv({}))
        try:
            logging.getLogger("blt.logging.fixture").warning("file fixture")
        finally:
            stop(state)
        assert "file fixture" in Path(root, "nested/log.txt").read_text()
        disabled = start(root, conv({"otlp": {"enable": False}}), conv({}))
        assert disabled.val_at(kw("provider")) is None
        stop(disabled)
        print("File logging and disabled telemetry passed", flush=True)

    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    requests = []
    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        def log_message(self, *_):
            pass
    server = HTTPServer(("127.0.0.1", 0), Collector)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for protocol in ["http/protobuf", "http/json"]:
            state = start(None, conv({"otlp": {"enable": True, "config": {
                "otel.exporter.otlp.logs.protocol": protocol,
                "otel.exporter.otlp.endpoint": f"http://127.0.0.1:{server.server_port}/ingest",
                "otel.exporter.otlp.logs.headers": "authorization=fixture%20token",
                "otel.exporter.otlp.logs.timeout": "2s",
                "otel.blrp.schedule.delay": "60s",
                "otel.service.name": "blt-logging-test"
            }}}), conv({}))
            try:
                exporter_module = ("opentelemetry.exporter.otlp.proto.http._log_exporter"
                                   if protocol == "http/protobuf" else
                                   "opentelemetry.exporter.otlp.json.http._log_exporter")
                exporter_type = importlib.import_module(exporter_module).OTLPLogExporter
                original_export = exporter_type.export
                exports = []
                def export_with_internal_log(exporter, *args, **kwargs):
                    exports.append(True)
                    logging.getLogger("blt.transport.fixture").warning("Transport implementation diagnostic")
                    return original_export(exporter, *args, **kwargs)
                before = len(requests)
                with patch.object(exporter_type, "export", export_with_internal_log):
                    logging.getLogger("blt.logging.fixture").warning("HTTP fixture")
                    assert state.val_at(kw("provider")).force_flush(5000)
                assert len(exports) == 1 and len(requests) == before + 1, "Exporter logs fed back into telemetry"
                path, headers, body = requests[-1]
                assert path == "/ingest/v1/logs", path
                assert headers["authorization"] == "fixture token", headers
                assert b"HTTP fixture" in body, body
            finally:
                stop(state)
            print(protocol, "transport passed", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)

    import grpc
    from concurrent.futures import ThreadPoolExecutor
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2, logs_service_pb2_grpc
    grpc_requests = []
    class Service(logs_service_pb2_grpc.LogsServiceServicer):
        def Export(self, request, context):
            grpc_requests.append(request)
            return logs_service_pb2.ExportLogsServiceResponse()
    grpc_server = grpc.server(ThreadPoolExecutor(max_workers=1))
    logs_service_pb2_grpc.add_LogsServiceServicer_to_server(Service(), grpc_server)
    port = grpc_server.add_insecure_port("127.0.0.1:0")
    grpc_server.start()
    state = start(None, conv({"otlp": {"enable": True, "config": {
        "otel.exporter.otlp.logs.protocol": "grpc",
        "otel.exporter.otlp.logs.endpoint": f"http://127.0.0.1:{port}",
        "otel.exporter.otlp.logs.timeout": 2000
    }}}), conv({}))
    try:
        logging.getLogger("blt.logging.fixture").warning("gRPC fixture")
        assert state.val_at(kw("provider")).force_flush(5000)
        assert grpc_requests and "gRPC fixture" in str(grpc_requests[-1])
    finally:
        stop(state)
        grpc_server.stop(0).wait()
    print("gRPC transport passed", flush=True)

if __name__ == "__main__":
    environment = {key: value for key, value in os.environ.items() if not key.startswith("OTEL_")}
    with patch.dict(os.environ, environment, clear=True):
        main()
