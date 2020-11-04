import boto3 
import json 
import logging 
import time 
import os 
import decimal 
import datetime
from boto3.dynamodb.conditions import Key, Attr

logger = logging.getLogger("handler_logger")
logger.setLevel(logging.DEBUG)

dynamodb = boto3.resource("dynamodb")

# Include fallback values in case these env variables are not set.
# This is primarily used when running tests.
CONNECTIONS_TABLE = os.getenv('CONNECTIONS_TABLE', 'photonranch-logstream-connections-dev')
LOGS_TABLE = os.getenv('LOGS_TABLE', 'photonranch-observatory-logs-dev')

table = dynamodb.Table(LOGS_TABLE)

################################################
##########     Helper Functions     ############
################################################

# Helper class to convert a DynamoDB item to JSON.
class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            if o % 1 > 0:
                return float(o)
            else:
                return int(o)
        return super(DecimalEncoder, self).default(o)

def http_response(status_code, body):
    if not isinstance(body, str):
        body = json.dumps(body, cls=DecimalEncoder)
    return {
        "statusCode": status_code, 
        'headers': {
            # Required for CORS support to work
            'Access-Control-Allow-Origin': '*',
            # Required for cookies, authorization headers with HTTPS
            'Access-Control-Allow-Credentials': 'true',
        },
        "body": body}

def _get_body(event):
    try:
        return json.loads(event.get("body", ""))
    except:
        logger.debug("event body could not be JSON decoded.")
        return {}

def _remove_all_connections():
    connectionsTable = dynamodb.Table(CONNECTIONS_TABLE)
    scan =connectionsTable.scan()
    with connectionsTable.batch_writer() as batch:
        for each in scan['Items']:
            batch.delete_item(
                Key={
                    'site': each['site'],
                    'ConnectionID': each['ConnectionID']
                }
            )

def get_ws_url_from_event(event):
    """
    Using the event that api-gateway websockets pass to the lambda, generate
    the websocket url used to respond.
    """
    return f"https://{os.getenv('WS_URL')}"

################################################
##########        Websockets        ############
################################################

def connection_manager(event, context):
    """
    Handles connecting and disconnecting for the Websocket
    """
    connectionID = event["requestContext"].get("connectionId")
    table = dynamodb.Table(CONNECTIONS_TABLE)

    print(json.dumps(event.get("queryStringParameters", []), indent=2))

    if event["requestContext"]["eventType"] == "CONNECT":
        logger.info("Connect requested")
        site = event["queryStringParameters"]["site"]

        # Add connectionID to the database
        table.put_item(Item={
            "site": site, 
            "ConnectionID": connectionID,
            "ConnectionStartedUTC": str(datetime.datetime.utcnow())
            })

        return http_response(200, "Connect successful.")

    elif event["requestContext"]["eventType"] in ("DISCONNECT", "CLOSE"):
        logger.info("Disconnect requested")

        # Get the details for the connection ID that is disconnecting
        connections = table.query(
            IndexName="ConnectionID",
            KeyConditionExpression=Key('ConnectionID').eq(connectionID)
        )

        for c in connections['Items']:
            # Remove each connection for the connectionID from the database
            table.delete_item(
                Key={
                    "site": c["site"],
                    "ConnectionID": c["ConnectionID"]
                }
            ) 
        
        return http_response(200, "Disconnect successful.")

    else:
        logger.error("Connection manager received unrecognized eventType '{}'")
        return http_response(500, "Unrecognized eventType.")


def send_to_connection(connection_id: str, data: dict, ws_url: str):

    gatewayapi = boto3.client( "apigatewaymanagementapi", endpoint_url=ws_url)

    try: 
        posted = gatewayapi.post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(data, cls=DecimalEncoder).encode('utf-8')
        )

    except Exception as e:
        print(f"Could not send to connection {connection_id}")
        print(e)

def send_to_all_connections(site: str, payload: dict, ws_url: str):

        users = get_online_users(site)
        connections = [user["ConnectionID"] for user in users if "ConnectionID" in user]

        # Send the message data to all connections
        logger.debug("Broadcasting message: {}".format(payload))
        for connectionID in connections:
            send_to_connection(connectionID, payload, ws_url)

################################################
##########      Core Functions      ############
################################################


def get_online_users(site):
    """
    This is the helper function that returns a list of dicts representing
    the users online in a given room.
    """

    table = dynamodb.Table(CONNECTIONS_TABLE)

    response = table.query(KeyConditionExpression=Key('site').eq(site))
    items = response.get("Items", [])

    connections = [
        {
            "ConnectionID": x["ConnectionID"],
        } 
        for x in items if "ConnectionID" in x
    ]
    return connections


def get_recent_logs(site, timestamp_max_age):

    logger.info("Retrieving most recent messages.")

    table = dynamodb.Table(LOGS_TABLE)
    response = table.query(
        KeyConditionExpression=Key('site').eq(site) & Key('timestamp').gt(timestamp_max_age)
    )
    items = response.get("Items", [])

    # Extract the relevant data and order chronologically
    messages = [{
        "message": x["message"], 
        "log_level": x.get("log_level", "info"),
        "timestamp": x["timestamp"]}
            for x in items]
    messages.reverse()

    return messages

def add_log_entry(entry):
    
    table = dynamodb.Table(LOGS_TABLE)

    # Add the new message to the database
    time_to_live = int(time.time()) + 7*86400 # ttl of 7 days 
    item = {
        "site": entry["site"],
        "message": entry["log_message"],
        "log_level": entry["log_level"],
        "timestamp": entry["timestamp"],
        "TimeToLive": time_to_live,
    } 
    table.put_item(Item=item)

################################################
##########     Handler Functions    ############
################################################

def get_online_users_handler(event, context):
    """
    This is the api endpoint which uses get_online_users().
    """
    site = event["queryStringParameters"].get("site", "error")
    if site == "error":
        return http_response(400, "Requires query string param 'site'.")
    users = get_online_users(site)
    return http_response(200, json.dumps(users, cls=DecimalEncoder))


def default_message_handler(event, context):
    """
    Send back error when unrecognized WebSocket action is received.
    """
    logger.info("Unrecognized WebSocket action received.")
    return http_response(400, "Unrecognized WebSocket action.")


def get_recent_logs_handler(event, context):
    print(event)

    timestamp_max_age = int(event["queryStringParameters"].get("after_time"))
    site = event["queryStringParameters"].get("site")

    recent_logs = get_recent_logs(site, timestamp_max_age=timestamp_max_age)

    return http_response(200, recent_logs)


def add_log_entry_handler(event, context):

    print(event)
    body = _get_body(event)

    entry = {
        "site": body["site"],
        "log_message": body["log_message"],
        "log_level": body.get("log_level", "info"),
        "timestamp": int(body["timestamp"])
    }
    add_log_entry(entry)

    return http_response(200, "Succesfully added new log message.")

def new_log_stream_handler(event, context):
    """
    sample event: 
    {'Records': [{
        eventID': '699f8495963197b9b17d91b2ec0946f0', 
        'eventName': 'INSERT', 
        'eventVersion': '1.1', 
        'eventSource': 'aws:dynamodb', 
        'awsRegion': 'us-east-1', 
        'dynamodb': {
            'ApproximateCreationDateTime': 1603317005.0, 
            'Keys': {
                'site': {'S': 'tst'}, 
                'timestamp': {'N': '123456789'}
            },
            'NewImage': {
                'site': {'S': 'tst'}, 
                'message': {'S': 'first log'}, 
                'timestamp': {'N': '123456789'}
            }, 
            'SequenceNumber': '100000000001547440520', 
            'SizeBytes': 60, 
            'StreamViewType': 'NEW_IMAGE'
        }, 
        'eventSourceARN': 'arn:aws:dynamodb:us-east-1:306389350997:table/photonranch-observatory-logs/stream/2020-10-21T21:47:47.149'
        }]}
    """
    print(event)

    record = event["Records"][0]

    for record in event["Records"]:

        # We only care about new messages; skip all others
        if record["eventName"] != "INSERT": 
            continue

        site = record["dynamodb"]["NewImage"]["site"]["S"]
        timestamp = record["dynamodb"]["NewImage"]["timestamp"]["N"]

        # Parse the message if it exists
        message = ""
        if "message" in record["dynamodb"]["NewImage"].keys():
            message = record["dynamodb"]["NewImage"]["message"]["S"]

        # Parse the log level if it exists; otherwise set to 'info'
        log_level = "info"
        if "log_level" in record["dynamodb"]["NewImage"].keys():
            log_level = record["dynamodb"]["NewImage"]["log_level"]["S"]

        print(f"record: {record}")
        print(f"site: {site}")
        print(f"timestamp: {timestamp}")
        print(f"message: {message}")
        print(f"log_level: {log_level}")

        # This will be sent to all subscribers
        data_to_send = {
            "site": site,
            "message": message, 
            "log_level": log_level,
            "timestamp": timestamp
        }
        ws_url = get_ws_url_from_event(event)

        # Send the new message to all subscribers
        send_to_all_connections(site, data_to_send, ws_url)


if __name__=="__main__":
    #_remove_all_connections()

    import requests

    def post_log_entry():
        url = "https://logs.photonranch.org/logs/newlog"
        body = json.dumps({
            "site": "tst",
            "log_message": """Here is a log sent with python.
            A multiline string.\nThis line used a newline character.""",
            "timestamp": time.time(),
        })
        resp = requests.post(url, body)
        print(resp)
    
    post_log_entry()

    pass

'''
[
    {
        'eventID': 
        'e30ffeb09553a38e38f2935a2a9f2b56', 
        'eventName': 'REMOVE', 
        'eventVersion': '1.1', 
        'eventSource': 'aws:dynamodb', 
        'awsRegion': 'us-east-1', 
        'dynamodb': {
            'ApproximateCreationDateTime': 1603750725.0, 
            'Keys': {
                'site': {'S': 'tst'}, 
                'timestamp': {'N': '223456799'}
            }, 
            'SequenceNumber': '23810200000000060219469036', 
            'SizeBytes': 22, 
            'StreamViewType': 'NEW_IMAGE'
        }, 
        'eventSourceARN': 'arn:aws:dynamodb:us-east-1:306389350997:table/photonranch-observatory-logs/stream/2020-10-21T21:47:47.149'
    }, 
    {
        'eventID': '99baeeb4a37a08c2995dc1df39750858', 
        'eventName': 'INSERT', 
        'eventVersion': '1.1', 
        'eventSource': 'aws:dynamodb', 
        'awsRegion': 'us-east-1', 
        'dynamodb': {
            'ApproximateCreationDateTime': 1603750730.0, 
            'Keys': {
                'site': {'S': 'tst'}, 
                'timestamp': {'N': '223456799'}
            }, 
            'NewImage': {
                'site': {'S': 'tst'}, 
                'message': {'S': 'third log'}, 
                'timestamp': {'N': '223456799'}
            }, 
            'SequenceNumber': '23810300000000060219470614', 
            'SizeBytes': 60, 
            'StreamViewType': 'NEW_IMAGE'
        }, 
        'eventSourceARN': 'arn:aws:dynamodb:us-east-1:306389350997:table/photonranch-observatory-logs/stream/2020-10-21T21:47:47.149'
    }, 
    {
        'eventID': 'ebcde40f9f7b5643a5d902c32663cbbf', 'eventName': 'INSERT', 'eventVersion': '1.1', 'eventSource': 'aws:dynamodb', 'awsRegion': 'us-east-1', 'dynamodb': {'ApproximateCreationDateTime': 1603750760.0, 'Keys': {'site': {'S': 'tst'}, 'timestamp': {'N': '0'}}, 'NewImage': {'site': {'S': 'tst'}, 'timestamp': {'N': '0'}}, 'SequenceNumber': '23810400000000060219482395', 'SizeBytes': 34, 'StreamViewType': 'NEW_IMAGE'}, 'eventSourceARN': 'arn:aws:dynamodb:us-east-1:306389350997:table/photonranch-observatory-logs/stream/2020-10-21T21:47:47.149'}, {'eventID': '429a283731e77afa575174ef5d0c7fbc', 'eventName': 'INSERT', 'eventVersion': '1.1', 'eventSource': 'aws:dynamodb', 'awsRegion': 'us-east-1', 'dynamodb': {'ApproximateCreationDateTime': 1603750851.0, 'Keys': {'site': {'S': 'asdf'}, 'timestamp': {'N': '1'}}, 'NewImage': {'site': {'S': 'asdf'}, 'timestamp': {'N': '1'}}, 'SequenceNumber': '23810500000000060219516315', 'SizeBytes': 38, 'StreamViewType': 'NEW_IMAGE'}, 'eventSourceARN': 'arn:aws:dynamodb:us-east-1:306389350997:table/photonranch-observatory-logs/stream/2020-10-21T21:47:47.149'}, {'eventID': '2d353b0eae4c5242e404d11382a11781', 'eventName': 'INSERT', 'eventVersion': '1.1', 'eventSource': 'aws:dynamodb', 'awsRegion': 'us-east-1', 'dynamodb': {'ApproximateCreationDateTime': 1603750960.0, 'Keys': {'site': {'S': 'asdf'}, 'timestamp': {'N': '12'}}, 'NewImage': {'site': {'S': 'asdf'}, 'message': {'S': 'hello world'}, 'timestamp': {'N': '12'}}, 'SequenceNumber': '23810600000000060219560697', 'SizeBytes': 56, 'StreamViewType': 'NEW_IMAGE'}, 'eventSourceARN': 'arn:aws:dynamodb:us-east-1:306389350997:table/photonranch-observatory-logs/stream/2020-10-21T21:47:47.149'}]

'''