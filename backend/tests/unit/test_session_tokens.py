from app.core.session_tokens import generate_token, hash_token, tokens_match


def test_generate_token_is_high_entropy_and_unique():
    tokens = {generate_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(t) >= 32 for t in tokens)


def test_hash_token_is_deterministic():
    token = generate_token()
    assert hash_token(token) == hash_token(token)


def test_hash_token_differs_for_different_tokens():
    assert hash_token(generate_token()) != hash_token(generate_token())


def test_hash_token_is_not_the_raw_token():
    token = generate_token()
    assert hash_token(token) != token


def test_tokens_match_true_for_the_correct_token():
    token = generate_token()
    assert tokens_match(token, hash_token(token))


def test_tokens_match_false_for_the_wrong_token():
    token = generate_token()
    other = generate_token()
    assert not tokens_match(other, hash_token(token))
