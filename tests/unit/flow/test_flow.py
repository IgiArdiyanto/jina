import os
import time
import unittest
from time import sleep

import pytest
import requests

from jina import JINA_GLOBAL
from jina.enums import FlowOptimizeLevel, SocketType
from jina.flow import Flow
from jina.main.checker import NetworkChecker
from jina.main.parser import set_pea_parser, set_ping_parser
from jina.main.parser import set_pod_parser
from jina.peapods.pea import BasePea
from jina.peapods.pod import BasePod
from jina.proto.jina_pb2 import Document
from tests import JinaTestCase, random_docs

cur_dir = os.path.dirname(os.path.abspath(__file__))


class FlowTestCase(JinaTestCase):

    def test_ping(self):
        a1 = set_pea_parser().parse_args([])
        a2 = set_ping_parser().parse_args(['0.0.0.0', str(a1.port_ctrl), '--print-response'])
        a3 = set_ping_parser().parse_args(['0.0.0.1', str(a1.port_ctrl), '--timeout', '1000'])

        with self.assertRaises(SystemExit) as cm:
            with BasePea(a1):
                NetworkChecker(a2)

        assert cm.exception.code == 0

        # test with bad addresss
        with self.assertRaises(SystemExit) as cm:
            with BasePea(a1):
                NetworkChecker(a3)

        assert cm.exception.code == 1

    def test_flow_with_jump(self):
        f = (Flow().add(name='r1', uses='_pass')
             .add(name='r2', uses='_pass')
             .add(name='r3', uses='_pass', needs='r1')
             .add(name='r4', uses='_pass', needs='r2')
             .add(name='r5', uses='_pass', needs='r3')
             .add(name='r6', uses='_pass', needs='r4')
             .add(name='r8', uses='_pass', needs='r6')
             .add(name='r9', uses='_pass', needs='r5')
             .add(name='r10', uses='_merge', needs=['r9', 'r8']))

        with f:
            f.dry_run()

        node = f._pod_nodes['gateway']
        assert node.head_args.socket_in == SocketType.PULL_CONNECT
        assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

        node = f._pod_nodes['r1']
        assert node.head_args.socket_in == SocketType.PULL_BIND
        assert node.tail_args.socket_out == SocketType.PUB_BIND

        node = f._pod_nodes['r2']
        assert node.head_args.socket_in == SocketType.SUB_CONNECT
        assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

        node = f._pod_nodes['r3']
        assert node.head_args.socket_in == SocketType.SUB_CONNECT
        assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

        node = f._pod_nodes['r4']
        assert node.head_args.socket_in == SocketType.PULL_BIND
        assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

        node = f._pod_nodes['r5']
        assert node.head_args.socket_in == SocketType.PULL_BIND
        assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

        node = f._pod_nodes['r6']
        assert node.head_args.socket_in == SocketType.PULL_BIND
        assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

        node = f._pod_nodes['r8']
        assert node.head_args.socket_in == SocketType.PULL_BIND
        assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

        node = f._pod_nodes['r9']
        assert node.head_args.socket_in == SocketType.PULL_BIND
        assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

        node = f._pod_nodes['r10']
        assert node.head_args.socket_in == SocketType.PULL_BIND
        assert node.tail_args.socket_out == SocketType.PUSH_BIND

        for name, node in f._pod_nodes.items():
            assert node.peas_args['peas'][0] == node.head_args
            assert node.peas_args['peas'][0] == node.tail_args

        f.save_config('tmp.yml')
        Flow.load_config('tmp.yml')

        with Flow.load_config('tmp.yml') as fl:
            fl.dry_run()

        self.add_tmpfile('tmp.yml')

    def test_simple_flow(self):
        bytes_gen = (b'aaa' for _ in range(10))

        def bytes_fn():
            for _ in range(100):
                yield b'aaa'

        f = (Flow()
             .add(uses='_pass'))

        with f:
            f.index(input_fn=bytes_gen)

        with f:
            f.index(input_fn=bytes_fn)

        with f:
            f.index(input_fn=bytes_fn)
            f.index(input_fn=bytes_fn)

        node = f._pod_nodes['gateway']
        assert node.head_args.socket_in == SocketType.PULL_CONNECT
        assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

        node = f._pod_nodes['pod0']
        assert node.head_args.socket_in == SocketType.PULL_BIND
        assert node.tail_args.socket_out == SocketType.PUSH_BIND

        for name, node in f._pod_nodes.items():
            assert node.peas_args['peas'][0] == node.head_args
            assert node.peas_args['peas'][0] == node.tail_args

    def test_load_flow_from_yaml(self):
        with open(os.path.join(cur_dir, '../yaml/test-flow.yml')) as fp:
            a = Flow.load_config(fp)
            with open(os.path.join(cur_dir, '../yaml/swarm-out.yml'), 'w') as fp, a:
                a.to_swarm_yaml(fp)
            self.add_tmpfile(os.path.join(cur_dir, '../yaml/swarm-out.yml'))

    def test_flow_identical(self):
        with open(os.path.join(cur_dir, '../yaml/test-flow.yml')) as fp:
            a = Flow.load_config(fp)

        b = (Flow()
             .add(name='chunk_seg', parallel=3)
             .add(name='wqncode1', parallel=2)
             .add(name='encode2', parallel=2, needs='chunk_seg')
             .join(['wqncode1', 'encode2']))

        a.save_config('test2.yml')

        c = Flow.load_config('test2.yml')

        assert a == b
        assert a == c

        self.add_tmpfile('test2.yml')

        with a as f:
            node = f._pod_nodes['gateway']
            assert node.head_args.socket_in == SocketType.PULL_CONNECT
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['chunk_seg']
            assert node.head_args.socket_in == SocketType.PULL_BIND
            assert node.head_args.socket_out == SocketType.ROUTER_BIND
            for arg in node.peas_args['peas']:
                assert arg.socket_in == SocketType.DEALER_CONNECT
                assert arg.socket_out == SocketType.PUSH_CONNECT
            assert node.tail_args.socket_in == SocketType.PULL_BIND
            assert node.tail_args.socket_out == SocketType.PUB_BIND

            node = f._pod_nodes['wqncode1']
            assert node.head_args.socket_in == SocketType.SUB_CONNECT
            assert node.head_args.socket_out == SocketType.ROUTER_BIND
            for arg in node.peas_args['peas']:
                assert arg.socket_in == SocketType.DEALER_CONNECT
                assert arg.socket_out == SocketType.PUSH_CONNECT
            assert node.tail_args.socket_in == SocketType.PULL_BIND
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['encode2']
            assert node.head_args.socket_in == SocketType.SUB_CONNECT
            assert node.head_args.socket_out == SocketType.ROUTER_BIND
            for arg in node.peas_args['peas']:
                assert arg.socket_in == SocketType.DEALER_CONNECT
                assert arg.socket_out == SocketType.PUSH_CONNECT
            assert node.tail_args.socket_in == SocketType.PULL_BIND
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

    def test_dryrun(self):
        f = (Flow()
             .add(name='dummyEncoder', uses=os.path.join(cur_dir, '../mwu-encoder/mwu_encoder.yml')))

        with f:
            f.dry_run()

    def test_pod_status(self):
        args = set_pod_parser().parse_args(['--parallel', '3'])
        with BasePod(args) as p:
            assert len(p.status) == p.num_peas
            for v in p.status:
                self.assertIsNotNone(v)

    def test_flow_no_container(self):
        f = (Flow()
             .add(name='dummyEncoder', uses=os.path.join(cur_dir, '../mwu-encoder/mwu_encoder.yml')))

        with f:
            f.index(input_fn=random_docs(10))

    def test_flow_yaml_dump(self):
        f = Flow(logserver_config=os.path.join(cur_dir, '../yaml/test-server-config.yml'),
                 optimize_level=FlowOptimizeLevel.IGNORE_GATEWAY,
                 no_gateway=True)
        f.save_config('test1.yml')

        fl = Flow.load_config('test1.yml')
        assert f.args.logserver_config == fl.args.logserver_config
        assert f.args.optimize_level == fl.args.optimize_level
        self.add_tmpfile('test1.yml')

    def test_flow_log_server(self):
        f = Flow.load_config(os.path.join(cur_dir, '../yaml/test_log_server.yml'))
        with f:
            self.assertTrue(hasattr(JINA_GLOBAL.logserver, 'ready'))

            # Ready endpoint
            a = requests.get(
                JINA_GLOBAL.logserver.address +
                '/status/ready',
                timeout=5)
            assert a.status_code == 200

            # YAML endpoint
            a = requests.get(
                JINA_GLOBAL.logserver.address +
                '/data/yaml',
                timeout=5)
            self.assertTrue(a.text.startswith('!Flow'))
            assert a.status_code == 200

            # Pod endpoint
            a = requests.get(
                JINA_GLOBAL.logserver.address +
                '/data/api/pod',
                timeout=5)
            self.assertTrue('pod' in a.json())
            assert a.status_code == 200

            # Shutdown endpoint
            a = requests.get(
                JINA_GLOBAL.logserver.address +
                '/action/shutdown',
                timeout=5)
            assert a.status_code == 200

            # Check ready endpoint after shutdown, check if server stopped
            with self.assertRaises(requests.exceptions.ConnectionError):
                a = requests.get(
                    JINA_GLOBAL.logserver.address +
                    '/status/ready',
                    timeout=5)

    def test_shards(self):
        f = Flow().add(name='doc_pb', uses=os.path.join(cur_dir, '../yaml/test-docpb.yml'), parallel=3,
                       separated_workspace=True)
        with f:
            f.index(input_fn=random_docs(1000), random_doc_id=False)
        with f:
            pass
        self.add_tmpfile('test-docshard-tmp')

    def test_py_client(self):
        f = (Flow().add(name='r1', uses='_pass')
             .add(name='r2', uses='_pass')
             .add(name='r3', uses='_pass', needs='r1')
             .add(name='r4', uses='_pass', needs='r2')
             .add(name='r5', uses='_pass', needs='r3')
             .add(name='r6', uses='_pass', needs='r4')
             .add(name='r8', uses='_pass', needs='r6')
             .add(name='r9', uses='_pass', needs='r5')
             .add(name='r10', uses='_merge', needs=['r9', 'r8']))

        with f:
            f.dry_run()
            from jina.clients import py_client
            py_client(port_expose=f.port_expose, host=f.host).dry_run(as_request='index')

        with f:
            node = f._pod_nodes['gateway']
            assert node.head_args.socket_in == SocketType.PULL_CONNECT
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['r1']
            assert node.head_args.socket_in == SocketType.PULL_BIND
            assert node.tail_args.socket_out == SocketType.PUB_BIND

            node = f._pod_nodes['r2']
            assert node.head_args.socket_in == SocketType.SUB_CONNECT
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['r3']
            assert node.head_args.socket_in == SocketType.SUB_CONNECT
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['r4']
            assert node.head_args.socket_in == SocketType.PULL_BIND
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['r5']
            assert node.head_args.socket_in == SocketType.PULL_BIND
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['r6']
            assert node.head_args.socket_in == SocketType.PULL_BIND
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['r8']
            assert node.head_args.socket_in == SocketType.PULL_BIND
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['r9']
            assert node.head_args.socket_in == SocketType.PULL_BIND
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['r10']
            assert node.head_args.socket_in == SocketType.PULL_BIND
            assert node.tail_args.socket_out == SocketType.PUSH_BIND

            for name, node in f._pod_nodes.items():
                assert node.peas_args['peas'][0] == node.head_args
                assert node.peas_args['peas'][0] == node.tail_args

    def test_dry_run_with_two_pathways_diverging_at_gateway(self):
        f = (Flow().add(name='r2', uses='_pass')
             .add(name='r3', uses='_pass', needs='gateway')
             .join(['r2', 'r3']))

        with f:
            node = f._pod_nodes['gateway']
            assert node.head_args.socket_in == SocketType.PULL_CONNECT
            assert node.tail_args.socket_out == SocketType.PUB_BIND

            node = f._pod_nodes['r2']
            assert node.head_args.socket_in == SocketType.SUB_CONNECT
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['r3']
            assert node.head_args.socket_in == SocketType.SUB_CONNECT
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            for name, node in f._pod_nodes.items():
                assert node.peas_args['peas'][0] == node.head_args
                assert node.peas_args['peas'][0] == node.tail_args

            f.dry_run()

    def test_dry_run_with_two_pathways_diverging_at_non_gateway(self):
        f = (Flow().add(name='r1', uses='_pass')
             .add(name='r2', uses='_pass')
             .add(name='r3', uses='_pass', needs='r1')
             .join(['r2', 'r3']))

        with f:
            node = f._pod_nodes['gateway']
            assert node.head_args.socket_in == SocketType.PULL_CONNECT
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['r1']
            assert node.head_args.socket_in == SocketType.PULL_BIND
            assert node.tail_args.socket_out == SocketType.PUB_BIND

            node = f._pod_nodes['r2']
            assert node.head_args.socket_in == SocketType.SUB_CONNECT
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['r3']
            assert node.head_args.socket_in == SocketType.SUB_CONNECT
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            for name, node in f._pod_nodes.items():
                assert node.peas_args['peas'][0] == node.head_args
                assert node.peas_args['peas'][0] == node.tail_args
            f.dry_run()

    def test_refactor_num_part(self):
        f = (Flow().add(name='r1', uses='_logforward', needs='gateway')
             .add(name='r2', uses='_logforward', needs='gateway')
             .join(['r1', 'r2']))

        with f:
            node = f._pod_nodes['gateway']
            assert node.head_args.socket_in == SocketType.PULL_CONNECT
            assert node.tail_args.socket_out == SocketType.PUB_BIND

            node = f._pod_nodes['r1']
            assert node.head_args.socket_in == SocketType.SUB_CONNECT
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['r2']
            assert node.head_args.socket_in == SocketType.SUB_CONNECT
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            for name, node in f._pod_nodes.items():
                assert node.peas_args['peas'][0] == node.head_args
                assert node.peas_args['peas'][0] == node.tail_args


    def test_refactor_num_part_proxy(self):
        f = (Flow().add(name='r1', uses='_logforward')
             .add(name='r2', uses='_logforward', needs='r1')
             .add(name='r3', uses='_logforward', needs='r1')
             .join(['r2', 'r3']))

        with f:
            node = f._pod_nodes['gateway']
            assert node.head_args.socket_in == SocketType.PULL_CONNECT
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['r1']
            assert node.head_args.socket_in == SocketType.PULL_BIND
            assert node.tail_args.socket_out == SocketType.PUB_BIND

            node = f._pod_nodes['r2']
            assert node.head_args.socket_in == SocketType.SUB_CONNECT
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            node = f._pod_nodes['r3']
            assert node.head_args.socket_in == SocketType.SUB_CONNECT
            assert node.tail_args.socket_out == SocketType.PUSH_CONNECT

            for name, node in f._pod_nodes.items():
                assert node.peas_args['peas'][0] == node.head_args
                assert node.peas_args['peas'][0] == node.tail_args


    def test_refactor_num_part_proxy_2(self):
        f = (Flow().add(name='r1', uses='_logforward')
             .add(name='r2', uses='_logforward', needs='r1', parallel=2)
             .add(name='r3', uses='_logforward', needs='r1', parallel=3, polling='ALL')
             .needs(['r2', 'r3']))

        with f:
            f.index_lines(lines=['abbcs', 'efgh'])

    def test_refactor_num_part_2(self):
        f = (Flow()
             .add(name='r1', uses='_logforward', needs='gateway', parallel=3, polling='ALL'))

        with f:
            f.index_lines(lines=['abbcs', 'efgh'])

        f = (Flow()
             .add(name='r1', uses='_logforward', needs='gateway', parallel=3))

        with f:
            f.index_lines(lines=['abbcs', 'efgh'])

    def test_index_text_files(self):

        def validate(req):
            for d in req.docs:
                self.assertNotEqual(d.text, '')

        f = (Flow(read_only=True).add(uses=os.path.join(cur_dir, '../yaml/datauriindex.yml'), timeout_ready=-1))

        with f:
            f.index_files('*.py', output_fn=validate, callback_on_body=True)

        self.add_tmpfile('doc.gzip')

    def test_flow_with_publish_driver(self):

        f = (Flow()
             .add(name='r2', uses='!OneHotTextEncoder')
             .add(name='r3', uses='!OneHotTextEncoder', needs='gateway')
             .join(needs=['r2', 'r3']))

        def validate(req):
            for d in req.docs:
                self.assertIsNotNone(d.embedding)

        with f:
            f.index_lines(lines=['text_1', 'text_2'], output_fn=validate, callback_on_body=True)

    def test_flow_with_modalitys_simple(self):
        def validate(req):
            for d in req.index.docs:
                self.assertTrue(d.modality in ['mode1', 'mode2'])

        def input_fn():
            doc1 = Document()
            doc1.modality = 'mode1'
            doc2 = Document()
            doc2.modality = 'mode2'
            doc3 = Document()
            doc3.modality = 'mode3'
            return [doc1, doc2, doc3]

        flow = Flow().add(name='chunk_seg', parallel=3, uses='_pass').\
            add(name='encoder12', parallel=2,
                uses='- !FilterQL | {lookups: {modality__in: [mode1, mode2]}, recur_range: [0, 1]}')
        with flow:
            flow.index(input_fn=input_fn, output_fn=validate)

if __name__ == '__main__':
    unittest.main()
