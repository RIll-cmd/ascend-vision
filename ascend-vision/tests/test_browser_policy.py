import pytest

from browser.policy import BrowserPolicy, PolicyDenied


@pytest.mark.parametrize('url', [
    'file:///C:/Users/owner/private.txt',
    'javascript:alert(1)',
    'http://localhost/admin',
    'http://localhost.localdomain/admin',
    'http://127.0.0.1:8000/',
    'http://10.0.0.2/',
    'http://172.16.0.4/',
    'http://192.168.1.5/',
    'http://169.254.169.254/latest/meta-data/',
    'http://[::1]/',
    'http://user:pass@example.org/',
])
def test_public_research_policy_blocks_local_private_and_non_web_targets(url):
    with pytest.raises(PolicyDenied):
        BrowserPolicy().validate_url(url)


def test_public_research_policy_allows_https_public_ip_literal():
    assert BrowserPolicy().validate_url('https://8.8.8.8/docs') == 'https://8.8.8.8/docs'


def test_public_research_policy_does_not_allow_unreviewed_form_writes():
    policy = BrowserPolicy()

    assert policy.allows_action('navigate', target_kind='page_link')
    assert policy.allows_action('observe', target_kind=None)
    assert policy.allows_action('scroll', target_kind=None)
    assert not policy.allows_action('fill', target_kind='textbox')
    assert not policy.allows_action('click', target_kind='button')
    assert not policy.allows_action('select', target_kind='combobox')
