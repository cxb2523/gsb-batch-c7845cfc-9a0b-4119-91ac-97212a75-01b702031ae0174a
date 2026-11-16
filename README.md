# robotframework-gevent
<!-- ALL-CONTRIBUTORS-BADGE:START - Do not remove or modify this section -->
[![All Contributors](https://img.shields.io/badge/all_contributors-1-orange.svg?style=flat-square)](#contributors-)
<!-- ALL-CONTRIBUTORS-BADGE:END -->
Run keywords asynchronously with the power of gevent


[![Version](https://img.shields.io/pypi/v/robotframework-gevent.svg)](https://pypi.python.org/pypi/robotframework-gevent)
![](https://raw.githubusercontent.com/eldaduzman/robotframework-gevent/main/docs/badges/coverage-badge.svg)
![](https://raw.githubusercontent.com/eldaduzman/robotframework-gevent/main/docs/badges/pylint.svg)
![](https://raw.githubusercontent.com/eldaduzman/robotframework-gevent/main/docs/badges/mutscore.svg)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)


## installation:
```bash
pip install robotframework-gevent
```

## Usage:

```robotframework
# simple-test.robot
*** Settings ***

Library             Collections
Library             String
Library             GeventLibrary
Library             RequestsLibrary


*** Test Cases ***
Test1
    [Documentation]    Simple test flow with gevent greenlets
    Log    Hello World
    Create Gevent Bundle    alias=alias1  # Create a bundle of coroutines
    Sleep    10s    alias=alias1    # run your synchronous keyword
    # register all your keywords as coroutines to the gevent bundle
    Add Coroutine    Sleep Wrapper    alias=alias1
    Add Coroutine    Sleep    20s    alias=alias1
    Add Coroutine    Sleep    10s    alias=alias1
    Add Coroutine    GET    https://jsonplaceholder.typicode.com/posts/1    alias=alias1
    Add Coroutine    Convert To Lower Case    UPPER

    # Run your coroutines and get the values by order
    ${values}    Run Coroutines    alias=alias1
    Log Many    @{values}

    # The 3rd coroutine was a request, take it's value
    ${jsonplaceholder_resp}    Get From List    ${values}    3

    # assert the returned response code to be 200
    Status Should Be    200    ${jsonplaceholder_resp}
    # assert that the returned `userId` field equals to 1
    Should Be Equal As Strings    1    ${jsonplaceholder_resp.json()['userId']}


*** Keywords ***
Sleep Wrapper
    Sleep    1s

```

### Fault tolerant runs (partial failures and timeouts)

By default `Run Coroutines` keeps the fail-fast behaviour: the first exception raised by a
coroutine is re-raised and the values of the whole bundle are lost.

When collecting data it is often better to settle every item individually.
Pass `raise_on_failure=${False}` (or `continue_on_failure=${True}`) to get one result
object per coroutine, in the order the coroutines were added:

```robotframework
    Create Gevent Bundle    alias=crawl
    Add Coroutine    Fetch Item    url=https://example.com/1    alias=crawl
    Add Coroutine    Fetch Item    url=https://example.com/broken    alias=crawl
    Add Coroutine    Fetch Item    url=https://example.com/slow    alias=crawl
    ${results}    Run Coroutines    alias=crawl    timeout=10    raise_on_failure=${False}

    FOR    ${result}    IN    @{results}
        IF    $result['status'] == 'completed'
            Log    Collected: ${result['value']}
        ELSE
            Log    Skipped ${result['keyword_name']}: ${result['reason']}
        END
    END
```

Each result is a dictionary (also usable as an object) with these fields:

| field | description |
| --- | --- |
| `keyword_name` | the keyword that was added as a coroutine |
| `status` | `completed`, `exception` or `timeout` |
| `value` | returned value for completed coroutines, otherwise `None` |
| `exception` / `exception_type` / `exception_message` | the failure details |
| `reason` | human readable failure reason, `None` when completed |
| `successful` / `failed` / `timed_out` | boolean shortcuts |

Coroutines that are still running when `timeout` expires are killed (they do not keep
writing in the background) and reported with the `timeout` status. In every case - success,
failure or timeout - the bundle is emptied at the end of the execution, so the same alias can
be populated and executed again.

```robotframework
    # after a run the bundle can be reused with the same alias
    Add Coroutine    Fetch Item    url=https://example.com/1    alias=crawl
    ${results}    Run Coroutines    alias=crawl    continue_on_failure=${True}
```


See -  [Keyword Documentation](https://eldaduzman.github.io/robotframework-gevent/GeventLibrary.html)
### run test:
```
>>> robot simple-test.robot
```
### Possible result

After test is completed go to `log.html`:

![](https://raw.githubusercontent.com/eldaduzman/robotframework-gevent/main/docs/images/Possible-Log-File.png)


Owing to the fact that keywords are executed asynchronously, we cannot know the order of keyword execution, so instead they are printed in a table format


### For more examples

go to [examples](https://github.com/eldaduzman/robotframework-gevent/tree/main/examples)
## Motivation

Modern software architecture is `event driven`, with many background process.
Servers are being more pro-active instead of re-active as we see in a `client server` architecture.

In order to test such systems, we need the ability to run coroutines in our test scripts.

With the power of [gevent](http://www.gevent.org/), we can run several coroutines in greenlets, so integrating them into our robotframework test script will provide super powers to our testing efforts!

## Why gevent?

Concurrency can be achieved in 3 different ways:

1.  Multiprocessing - running each task in it's own `process`.
    The cons of such an approach would be massive consumption of resources, namely CPU and memory, as this means to allocate an entire `memory heap` to each task.
    Another problem is a possible need for `Inter-Process Communication (IPC)` that might be costly.

2.  Multithreading - running each task in a `thread`.
    Unlike multiprocessing, now all tasks run on the same memory heap and separated by threads, which the CPU coordinates using `round-robin`.
    However, python's  `Global Interpreter Lock` (GIL) prevents these threads from acting concurrently, it might perform context switching when IO operation occurs but there's no control for that.


3.  Asynchronous IO - running all tasks on a single thread, while IO operations won't block the progress of the program, while code execution is committed by an   `event loop` that `selects` between attached `coroutines`.
    This is highly efficient in resources consumption when compared to multithreading and multiprocessing, but it requires some modifications to the original code.
    `Blocking` IO statements can hog the event loop and the code will not be concurrent.
    `gevent` allows programmers to write seemingly regular "blocking" python code, but it will enforce asynchronous IO compliance by `monkey patching`

## File structure
```

|   LICENSE
|   .gitignore
|   .pylintrc
|   pyproject.toml
|   poetry.lock
|   README.md

|           
+---src
|   \---GeventLibrary
|       |   \---exceptions
|       |       |   __init__.py
|       |   \---keywords
|       |       |   __init__.py
|       |       |   gevent_keywords.py
|       |   __init__.py
|       |   gevent_library.py
|               
+---atests
|   |   __init__.robot
|   |   simple-test.robot
|   |   
|   \---utests
|       |   __init__.py
|       |   test_bundle_creation.py

```
## Code styling
### `black` used for auto-formatting code [read](https://pypi.org/project/black/),
### `pylint` used for code linting and pep8 compliance [read](https://pypi.org/project/pylint/),
### `mypy` used for type hinting [read](https://pypi.org/project/mypy/),
### `robocop` static code analyzer for robotframework [read](https://pypi.org/project/robotframework-robocop/),
### `perflint` pylint extension for performance linting [read](https://betterprogramming.pub/use-perflint-a-performance-linter-for-python-eae8e54f1e99)
### `cosmic-ray` Python tool for mutation testing [read](https://python.plainenglish.io/python-mutation-testing-with-cosmic-ray-4b78eb9e0676)

## links
1. [Robotframework](https://robotframework.org/)
2. [gevent](http://www.gevent.org/)
## Contributors ✨

Thanks goes to these wonderful people ([emoji key](https://allcontributors.org/docs/en/emoji-key)):

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center"><a href="https://github.com/MalikMlitat"><img src="https://avatars.githubusercontent.com/u/19836100?v=4?s=100" width="100px;" alt="MalikMlitat"/><br /><sub><b>MalikMlitat</b></sub></a><br /><a href="https://github.com/eldaduzman/robotframework-gevent/commits?author=MalikMlitat" title="Documentation">📖</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

This project follows the [all-contributors](https://github.com/all-contributors/all-contributors) specification. Contributions of any kind welcome!