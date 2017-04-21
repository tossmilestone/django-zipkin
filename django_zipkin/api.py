import struct
import socket
import time
import base64
import logging

from thrift.protocol import TBinaryProtocol
from thrift.transport import TTransport

import constants
import defaults as settings
from data_store import default as default_store
from _thrift.zipkinCore.ttypes import Annotation, BinaryAnnotation, Endpoint, AnnotationType, Span


class ZipkinApi(object):
    def __init__(self, store=None, service_name=None, writer=None):
        self.store = store or default_store
        self.endpoint = Endpoint(
            ipv4=self._get_my_ip(),
            port=None,
            service_name=service_name or settings.ZIPKIN_SERVICE_NAME
        )
        self.writer = writer

    def record_event(self, message, duration=None):
        self.store.record(self._build_annotation(message, duration))

    def record_key_value(self, key, value):
        self.store.record(self._build_binary_annotation(key, value))

    def set_rpc_name(self, name):
        self.store.set_rpc_name(name)

    def build_log_message(self):
        trans = TTransport.TMemoryBuffer()
        protocol = TBinaryProtocol.TBinaryProtocolAccelerated(trans=trans)
        self._build_span().write(protocol)
        return base64.b64encode(trans.getvalue())

    def submit_span(self):
        self.writer.write(self._build_span())
        self.store.clear()

    def get_headers_for_downstream_request(self):
        try:
            data = self.store.get()
            headers = {
                constants.TRACE_ID_HDR_NAME: data.trace_id.get_hex() if data.trace_id is not None else None,
                constants.SPAN_ID_HDR_NAME: data.span_id.get_hex() if data.span_id is not None else None,
                constants.SAMPLED_HDR_NAME: self._bool_to_str_true_false(data.sampled),
                constants.FLAGS_HDR_NAME: self._bool_to_str_1_0(data.flags)
            }
            if data.parent_span_id is not None:
                headers[constants.PARENT_SPAN_ID_HDR_NAME] = data.parent_span_id.get_hex()
            for key in headers.keys():
                if headers[key] is None:
                    del headers[key]
            return headers
        except Exception:
            logging.root.exception("failed_to_build_downstream_request_headers")
            return {}

    def _bool_to_str_true_false(self, b):
        if b:
            return 'true'
        return 'false'

    def _bool_to_str_1_0(self, b):
        if b:
            return '1'
        return '0'

    def _get_my_ip(self):
        try:
            return self._ipv4_to_long(socket.gethostbyname(socket.gethostname()))
        except Exception:
            return None

    def _build_span(self):
        zipkin_data = self.store.get()
        return Span(
            id=zipkin_data.span_id.get_binary(),
            trace_id=zipkin_data.trace_id.get_binary(),
            parent_id=zipkin_data.parent_span_id.get_binary() if zipkin_data.parent_span_id is not None else None,
            name=self.store.get_rpc_name(),
            annotations=self.store.get_annotations(),
            binary_annotations=self.store.get_binary_annotations()
        )

    def _build_annotation(self, value, duration=None):
        if isinstance(value, unicode):
            value = value.encode('utf-8')
        return Annotation(time.time() * 1000 * 1000, str(value), self.endpoint, duration)

    def _build_binary_annotation(self, key, value):
        annotation_type = self._binary_annotation_type(value)
        formatted_value = self._format_binary_annotation_value(value, annotation_type)
        return BinaryAnnotation(key, formatted_value, annotation_type, self.endpoint)

    @classmethod
    def _binary_annotation_type(cls, value):
        if isinstance(value, str) or isinstance(value, unicode):
            return AnnotationType.STRING
        if isinstance(value, float):
            return AnnotationType.DOUBLE
        if isinstance(value, bool):
            return AnnotationType.BOOL
        if isinstance(value, int) or isinstance(value, long):
            # TODO: make this more granular to preserve network bytes
            return AnnotationType.I64

    @classmethod
    def _format_binary_annotation_value(cls, value, type):
        number_formats = {
            AnnotationType.I16: 'h',
            AnnotationType.I32: 'i',
            AnnotationType.I64: 'q',
            AnnotationType.DOUBLE: 'd'
        }
        if type == AnnotationType.STRING:
            if isinstance(value, unicode):
                return value.encode('utf-8')
            return str(value)
        if type == AnnotationType.BOOL:
            if value:
                return '1'
            else:
                return '0'
        if type in number_formats:
            return struct.pack('!' + number_formats[type], value)
        return 'zipkin_cat failed to serialize type %s value %s' % (type, value)

    @staticmethod
    def _ipv4_to_long(ip):
        packed_ip = socket.inet_aton(ip)
        return struct.unpack("!i", packed_ip)[0]


api = ZipkinApi(default_store)
