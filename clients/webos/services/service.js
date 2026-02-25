/* Copyright (c) 2026 Panayotis Katsaloulis */
/* SPDX-License-Identifier: AGPL-3.0-or-later */

var Service = require('webos-service');
var dgram = require('dgram');

var service = new Service('com.pytr.tv.service');
var activeSocket = null;

service.register('discover', function (message) {
    var servers = [];
    var socket = dgram.createSocket('udp4');
    activeSocket = socket;

    socket.on('error', function (err) {
        socket.close();
        activeSocket = null;
        message.respond({ returnValue: true, servers: servers });
    });

    socket.on('message', function (msg) {
        try {
            var data = JSON.parse(msg.toString());
            if (data.Address) {
                // Deduplicate by address
                var exists = servers.some(function (s) { return s.Address === data.Address; });
                if (!exists) servers.push(data);
            }
        } catch (e) {
            // Ignore malformed responses
        }
    });

    socket.bind(function () {
        socket.setBroadcast(true);
        var probe = Buffer.from('who is PytrServer?');
        socket.send(probe, 0, probe.length, 5444, '255.255.255.255');

        // Send a second probe after 500ms for reliability
        setTimeout(function () {
            try { socket.send(probe, 0, probe.length, 5444, '255.255.255.255'); }
            catch (e) { /* socket may be closed */ }
        }, 500);

        // Collect responses for 3 seconds
        setTimeout(function () {
            try { socket.close(); } catch (e) {}
            activeSocket = null;
            message.respond({ returnValue: true, servers: servers });
        }, 3000);
    });
});

service.register('stopDiscovery', function (message) {
    if (activeSocket) {
        try { activeSocket.close(); } catch (e) {}
        activeSocket = null;
    }
    message.respond({ returnValue: true });
});
