import http.client
import unittest
from unittest.mock import patch
import microloop_resume_20260910 as resume


class RecoveryTests(unittest.TestCase):
    def test_incomplete_read_normalized(self):
        with patch.object(resume.UrllibHTTPTransport, 'post', side_effect=http.client.IncompleteRead(b'')):
            with self.assertRaises(resume.TransportError) as ctx:
                resume.CompleteReadTransport().post()
            self.assertEqual(ctx.exception.category, 'network_io')
            self.assertTrue(ctx.exception.delivery_ambiguous)

    def test_complete_response_unchanged(self):
        response = object()
        with patch.object(resume.UrllibHTTPTransport, 'post', return_value=response):
            self.assertIs(resume.CompleteReadTransport().post(), response)

    def test_unrelated_error_not_retried(self):
        with patch.object(resume.UrllibHTTPTransport, 'post', side_effect=ValueError('configuration')):
            with self.assertRaises(ValueError): resume.CompleteReadTransport().post()


if __name__ == '__main__': unittest.main()
