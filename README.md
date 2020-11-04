# photonranch-userstatus

This is a serverless project that deploys a websocket api used to send messages
from an observatory to the frontend in real time. It is reminiscent of a logging
pipeline, and messages are presented as timestamped messages with a priority
status (eg. debug, info, warn, error, or critical).

The original task was streaming logs from the observatory so operators could
more easily debug/troubleshoot from just the frontend. But we've pivoted to
using this as a way to send more general messages to the user instead. The
original name was 'logstream', and the backend resources are still named with
that convention in mind (even though the repository name has diverged).

## Architecture

This serverless app creates api gateway instances, one for http and one for
websockets. These both route requests to a layer of lambda functions, which
interact with two dynamodb tables: one for storing messages, and one for
keeping track of websocket connections.

The messages table adds a TTL column which expires the messages after a few
days. We decided that it is not worth the maintenance burden to archive
all our messages; they are treated as transient notifications only.

A basic architecture diagram is provided below. Note that there are additional
endpoints and lambda functions not described, but this tells the basic idea.

![alt text](https://i.imgur.com/T0OwvXi.png)

## Usage

Depending on your setup, the serverless project will deploy with a unique 
endpoint url. For photon ranch (and for the following examples), we will 
use the base url `https://logs.photonranch.org/logs`.

### Example: sending a message

```python
import time, reqeusts, json
def send_log_to_frontend():
    url = "https://logs.photonranch.org/logs/newlog"
    body = json.dumps({
        "site": "saf",
        "log_message": "Here is a log sent with python.",
        "log_level": "info",
        "timestamp": time.time(),
    })
    resp = requests.post(url, body)
    print(resp)
```

There are four parameters supplied in the body:

- site (str): code for the site that will display the message.
- log_message (str): this content that the user will read.
- log_level (str): can be ["debug", "info", "warning", "error", "critical"]
following the python logging convention. Default (if none provided) is info.
- timestamp (int): unix timestamp in seconds. Messages are sorted and displayed
chronologically using this value, and the hh:mm time prefixes the message
display.

### Receiving a message

Websocket messages will arrive with the following structure:

```json
{
    "site":"tst",
    "message":"This is a log message for testing.\nIt is sent from the frontend.",
    "log_level":"warning",
    "timestamp":"1604514063"
}
```

### More information

For a complete look at the available endpoints, refer to `serverless.yml` which
defines all the infrastructure that this app deploys.
