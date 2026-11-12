def test_postgres_container(request):
    request.getfixturevalue('postgres_container')


def test_redis_container(request):
    request.getfixturevalue('redis_container')


def test_seaweedfs(request):
    request.getfixturevalue('seaweedfs')


def test_unit():
    pass
