"""Fixture implementations contributed through the same contract as addon defaults."""

SETTINGS = {
    "ANGEE_VCS_BACKEND_CLASSES.stub": "tests.conftest.StubVCSBackend",
    "ANGEE_INFERENCE_BACKEND_CLASSES.stub_inference": "tests.conftest.StubInferenceBackend",
    "ANGEE_CHANNEL_BACKEND_CLASSES.fake_live": "tests.pairing_backend.FakePairingBackend",
    "ANGEE_POSTS_FEED_BACKEND_CLASSES.stub": "tests.conftest.StubFeedBackend",
}
