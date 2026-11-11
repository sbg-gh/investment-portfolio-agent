import threading
import unittest

from portfolio_agent.web.worker import Direct, Worker, WorkerBusy


class WorkerTests(unittest.TestCase):
    def test_resource_is_built_and_used_on_one_dedicated_thread(self):
        worker = Worker(lambda: {'thread': threading.get_ident()})
        self.addCleanup(worker.close)
        seen = {worker.submit(lambda r: (r['thread'], threading.get_ident())) for _ in range(5)}
        self.assertEqual(len(seen), 1)
        build, use = seen.pop()
        self.assertEqual(build, use)
        self.assertNotEqual(use, threading.get_ident())

    def test_exceptions_propagate_and_worker_survives(self):
        worker = Worker(lambda: None)
        self.addCleanup(worker.close)
        with self.assertRaises(PermissionError):
            worker.submit(lambda r: (_ for _ in ()).throw(PermissionError('x')))
        self.assertEqual(worker.submit(lambda r: 5), 5)

    def test_sqlite_store_is_usable_from_the_worker(self):
        from portfolio_agent.core import Store
        worker = Worker(Store)
        self.addCleanup(worker.close)
        worker.submit(lambda store: store.create_user('u'))
        self.assertEqual(worker.submit(lambda store: store.db.execute('select id from users').fetchall()),
                         [('u',)])

    def test_queue_is_bounded(self):
        gate, running = threading.Event(), threading.Event()
        worker = Worker(lambda: None, max_pending=1)
        self.addCleanup(worker.close)
        self.addCleanup(gate.set)

        def block(_):
            running.set()
            gate.wait(5)
        first = threading.Thread(target=lambda: worker.submit(block))
        first.start()
        self.assertTrue(running.wait(5))
        second = threading.Thread(target=lambda: worker.submit(lambda r: 1))
        second.start()
        for _ in range(500):
            if worker._queue.full():
                break
            threading.Event().wait(0.01)
        with self.assertRaises(WorkerBusy):
            worker.submit(lambda r: 1)
        gate.set()
        first.join()
        second.join()

    def test_closed_worker_refuses(self):
        worker = Worker(lambda: None)
        worker.close()
        with self.assertRaises(WorkerBusy):
            worker.submit(lambda r: 1)

    def test_factory_failure_surfaces(self):
        with self.assertRaises(RuntimeError):
            Worker(lambda: (_ for _ in ()).throw(RuntimeError('boom')))

    def test_direct_calls_inline(self):
        self.assertEqual(Direct(7).submit(lambda r: r + 1), 8)


if __name__ == '__main__':
    unittest.main()
