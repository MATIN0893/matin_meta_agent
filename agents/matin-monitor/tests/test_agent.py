import unittest
from core.agent import AutonomousWorker

class TestAgent(unittest.TestCase):
    def test_worker_cycle(self):
        worker = AutonomousWorker('TestAgent')
        res = worker.perform_cycle()
        self.assertEqual(res['status'], 'healthy')
        self.assertEqual(res['cycle'], 1)

if __name__ == '__main__':
    unittest.main()
