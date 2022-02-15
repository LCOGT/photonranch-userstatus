import time
import pytest
import boto3
import json
from boto3.dynamodb.conditions import Key, Attr

from handler import get_online_users
from handler import get_recent_logs
from handler import add_log_entry

from handler import get_online_users_handler
from handler import default_message_handler
from handler import get_recent_logs_handler
from handler import add_log_entry_handler
from handler import new_log_stream_handler

dynamodb = boto3.resource("dynamodb")

CONNECTIONS_TABLE = "photonranch-logstream-connections-dev"
LOGS_TABLE = "photonranch-observatory-logs-dev"


def test_get_recent_logs():

    site = "tst"
    timestamp_max_age = 0
    
    recent_logs = get_recent_logs(site, timestamp_max_age)
    assert isinstance(recent_logs, list)

def test_add_log_entry():

    # This is the sample content to add
    entry = {
        "site": "tst",
        "log_message": "This message is a result of tests running in the backend.",
        "log_level": "debug",
        "timestamp": int(time.time())
    }

    # The function we're testing
    add_log_entry(entry)

    # Try and retrieve the message we added
    table = dynamodb.Table(LOGS_TABLE)
    response = table.query(
        KeyConditionExpression=Key('site').eq(entry["site"]) \
            & Key('timestamp').eq(entry["timestamp"])
    )
    response_obj = response["Items"][0]

    # Duplicate the format of the original submission and compare. 
    retrieved_object = {
        "site": response_obj["site"],
        "log_message": response_obj["message"],
        "log_level": response_obj["log_level"],
        "timestamp": response_obj["timestamp"],
    }
    assert retrieved_object == entry


######################################################
##########     Test Handler Functions     ############
######################################################


def test_get_online_users_handler():
    event = {
        "queryStringParameters": {
            "site": "tst",
        }
    }
    context = {}
    response = get_online_users_handler(event, context)
    assert response["statusCode"] == 200

def test_default_message_handler():
    event = {}
    context = {}
    response = default_message_handler(event, context)
    assert response["statusCode"] == 400

def test_get_recent_logs_handler():
    event = {
        "queryStringParameters": {
            "after_time": 0,
            "site": "tst",
        }
    }
    context = {}
    response = get_recent_logs_handler(event, context)
    assert response["statusCode"] == 200


def test_add_log_entry_handler():
    entry = {
        "site": "tst",
        "log_message": "This message is a result of tests running in the backend.",
        "log_level": "debug",
        "timestamp": int(time.time())
    }
    event = { "body": json.dumps(entry) }
    context = {}

    # The function we're testing
    add_log_entry_handler(event,context)

    # Try and retrieve the message we added
    table = dynamodb.Table(LOGS_TABLE)
    response = table.query(
        KeyConditionExpression=Key('site').eq(entry["site"]) \
            & Key('timestamp').eq(entry["timestamp"])
    )
    response_obj = response["Items"][0]

    # Duplicate the format of the original submission and compare. 
    retrieved_object = {
        "site": response_obj["site"],
        "log_message": response_obj["message"],
        "log_level": response_obj["log_level"],
        "timestamp": response_obj["timestamp"],
    }
    assert retrieved_object == entry

def test_new_log_stream_handler():

    # sample event: 
    creation_timestamp = int(time.time())
    event = {
        'Records': [
            {
                'eventID': '699f8495963197b9b17d91b2ec0946f0', 
                'eventName': 'INSERT', 
                'eventVersion': '1.1', 
                'eventSource': 'aws:dynamodb', 
                'awsRegion': 'us-east-1', 
                'dynamodb': {
                    'ApproximateCreationDateTime': creation_timestamp, 
                    'Keys': {
                        'site': {'S': 'tst'}, 
                        'timestamp': {'N': str(creation_timestamp)}
                    },
                    'NewImage': {
                        'site': {'S': 'tst'}, 
                        'message': {'S': 'first log'}, 
                        'timestamp': {'N': str(creation_timestamp)}
                    }, 
                    'SequenceNumber': '100000000001547440520', 
                    'SizeBytes': 60, 
                    'StreamViewType': 'NEW_IMAGE'
                }, 
                'eventSourceARN': 'arn:aws:dynamodb:us-east-1:306389350997:table/photonranch-observatory-logs/stream/2020-10-21T21:47:47.149'
            }
        ]
    }
    context = {}
    new_log_stream_handler(event, context)
    
    assert True # acceptable as long as there are no exceptions. 
