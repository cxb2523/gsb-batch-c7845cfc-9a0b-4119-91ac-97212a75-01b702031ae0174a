# Copyright (c) Siemens 2021
# This file is subject to the terms and conditions of the MIT License.  
# See LICENSE file in the top-level directory.

"""Module Data Analytics.

This module consists of DataGenerator class and also the function to generate
bivariate normal distributed datasets.

"""

import paho.mqtt.client as mqtt
import sys
import logging
import statistics
import json

BROKER_ADDRESS='ie-databus'
BROKER_PORT=1883
MICRO_SERVICE_NAME = 'data-analytics'
""" Broker user and password for authtentification"""
USERNAME='edge'
PASSWORD='edge'

class DataAnalyzer():
    """
    Data Analyzer connects to mqtt broker and waits for new
    input data to calculate KPIs.

    """

    def __init__(self, logger_parent):
        """ Starts the instantiated object with a proper logger """
        
        logger_name = '{}.{}'.format(logger_parent,__name__)
        self.logger = logging.getLogger(logger_name)
        self.client = mqtt.Client(MICRO_SERVICE_NAME)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_subscribe = self.on_subscribe
        self.client.on_message = self.on_message
        self.topic_callback = dict()

    def on_connect(self, client, userdata, flags, rc):
        self.logger.info('Connected successfully to broker, response code {}'.format(rc))

    def on_disconnect(self, client, userdata, rc):
        if rc != 0:
            self.logger.warning('Connection ended unexpectedly from broker, error code {}'.format(rc))


    def on_subscribe(self, client, userdata, mid, granted_qos):
        
        self.logger.info('successfully subscribed ')

    def on_message(self, client, userdata, message):
        self.logger.info('New message received on topic: {}'.format(message.topic))
        try:
            new_msg = json.loads(message.payload)
        except (ValueError, TypeError) as err:
            # Malformed payloads must not kill the MQTT callback thread.
            self.logger.error('Skipping message on topic {}: payload is not valid JSON: {}'.format(message.topic, err))
            return
        self.logger.info('new message: {}'.format(new_msg))
        try:
            self.topic_callback[message.topic](new_msg)
        except Exception as err:
            self.logger.error('An error ocurred while hanlding new message of {}: {}'.format(message.topic, err))

    def subscribe(self, topic, callback):
        """ Subscribes to given topic, assigning a callback function that
        handles the received payload

        :topic:     string with the topic to subscribe
        :callback:  function to assign the payload received
        """
        self.topic_callback.update({topic:callback})
        self.client.subscribe(topic)

    # Callback function for MQTT topic 'StandardKpis'
    def standard_kpis(self, payload):
        values = [key['_value'] for key in payload]
        name = [key['_measurement'] for key in payload]
        self.logger.info('name is: {}'.format(name))
        # Calculate standard KPIs
        result = {
            'mean_result' : statistics.mean(values),
            'median_result' : statistics.median(values),
            'stddev_result' : statistics.stdev(values),
            'name' : payload[0]['_measurement'],
        }
        self.logger.info('mean calculated: {}'.format(statistics.mean(values)))
        self.logger.info('median calculated: {}'.format(statistics.median(values)))
        self.logger.info('stddev calculated: {} \n ======='.format(statistics.stdev(values)))
        # publish results back on MQTT topic 'StandardKpiResult'
        self.client.publish(topic='StandardKpiResult', payload=json.dumps(result))
        return

#   Callback function for MQTT topic 'Mean' subscription
    def power_mean(self, payload):
        self.logger.info('calculating power mean...')

        # Validate the batch structure before touching any data. A malformed
        # or empty batch is logged and skipped: no MeanResult is published and
        # the service keeps running instead of raising.
        if not isinstance(payload, dict):
            self.logger.error('power_mean: skipping batch - payload is not an object: %r', payload)
            return

        missing_fields = [
            key for key in ('sample_number', 'current_drive3_batch', 'voltage_drive3_batch')
            if key not in payload
        ]
        if missing_fields:
            self.logger.error('power_mean: skipping batch - missing field(s): %s', missing_fields)
            return

        sample_number = payload['sample_number']
        current_batch = payload['current_drive3_batch']
        voltage_batch = payload['voltage_drive3_batch']

        if not isinstance(current_batch, list) or not isinstance(voltage_batch, list):
            self.logger.error(
                'power_mean: skipping batch - current_drive3_batch and '
                'voltage_drive3_batch must be lists (got %s and %s)',
                type(current_batch).__name__, type(voltage_batch).__name__
            )
            return

        if len(current_batch) == 0 or len(voltage_batch) == 0:
            self.logger.error(
                'power_mean: skipping batch - empty pairing (current: %d samples, voltage: %d samples)',
                len(current_batch), len(voltage_batch)
            )
            return

        if len(current_batch) != len(voltage_batch):
            self.logger.error(
                'power_mean: skipping batch - batch lengths differ (current: %d, voltage: %d)',
                len(current_batch), len(voltage_batch)
            )
            return

        if not isinstance(sample_number, int) or isinstance(sample_number, bool) or sample_number <= 0:
            self.logger.error(
                'power_mean: skipping batch - invalid sample_number: %r', sample_number
            )
            return

        if sample_number != len(current_batch):
            self.logger.error(
                'power_mean: skipping batch - sample_number (%d) does not match '
                'the number of paired samples (%d)',
                sample_number, len(current_batch)
            )
            return

        try:
            current_values = [item['_value'] for item in current_batch]
            voltage_values = [item['_value'] for item in voltage_batch]
        except (KeyError, TypeError) as err:
            self.logger.error('power_mean: skipping batch - sample is missing _value field: %s', err)
            return

        # Calculate mean of power over the timestamp-aligned paired samples
        power_batch_sum = sum([current * voltage for current, voltage in zip(current_values, voltage_values)])

        power_mean = round((power_batch_sum / sample_number), 2)
        self.logger.info("power mean result: {}\n".format(power_mean))

        result = {
            'power_mean_result': power_mean,
            'name': 'powerdrive3_mean',
        }
        # publish result back on MQTT topic 'MeanResult'
        self.client.publish(topic='MeanResult', payload=json.dumps(result))
        return

    def handle_data(self):        
        """
        Starts the connection to MQTT broker and subscribe to respective
        topics.

        """
        
        self.logger.info('Preparing Mqtt Connection')
        try:
            self.client.username_pw_set(USERNAME, PASSWORD)
            self.client.connect(BROKER_ADDRESS)
            self.client.loop_start()
            self.logger.info('Subscribe to topic StandardKpis')
            self.subscribe(topic='StandardKpis', callback=self.standard_kpis)
            self.logger.info('Subscripe to topic Mean')
            self.subscribe(topic='Mean', callback=self.power_mean)
            self.logger.info('Finished subscription to topics')
            

        except Exception as e:
            self.logger.error(str(e))
