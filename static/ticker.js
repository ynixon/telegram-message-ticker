$(document).ready(function () {
    // Message age limit in hours (0.25 = 15 minutes)
    var message_age_limit_in_hours = 0.25;
    console.log("Message age limit set to:", message_age_limit_in_hours, "hours");

    // Keep the screen awake using the Screen Wake Lock API
    var wakeLock = null;
    async function requestWakeLock() {
        try {
            if ('wakeLock' in navigator) {
                wakeLock = await navigator.wakeLock.request('screen');
                console.log('Screen Wake Lock acquired');
                wakeLock.addEventListener('release', function() {
                    console.log('Screen Wake Lock released');
                });
            }
        } catch (err) {
            console.warn('Wake Lock failed:', err);
        }
    }
    requestWakeLock();
    // Re-acquire wake lock when page becomes visible again
    document.addEventListener('visibilitychange', function() {
        if (document.visibilityState === 'visible') requestWakeLock();
    });

    // Socket.IO configuration with reconnection settings
    var socket = io({
        reconnection: true,             // Ensure reconnection is enabled
        reconnectionAttempts: Infinity, // Retry indefinitely until connected
        reconnectionDelay: 1000,        // Wait 1 second before the first attempt
        reconnectionDelayMax: 5000,     // Maximum delay between retries is 5 seconds
        timeout: 20000,                 // Wait 20 seconds for a response from the server
    });

    var currentIndex = 0;
    var messages = [];
    var timeoutId = null;
    var backupTimeoutId = null;
    var isDisplaying = false;
    var totalMessagesReceived = 0;
    var isConnected = true;  // Flag to track connection state
    var hasInitialMessagesLoaded = false; // Flag to prevent re-processing

    // Set message_age_limit to match the server configuration (in hours)
    var message_age_limit = message_age_limit_in_hours; // 0.25 = 15 minutes

    var isRefreshing = false;  // Guard: skip cleanup while refresh is in flight

    // Function to remove old messages
    function removeOldMessages() {
        if (isRefreshing) return;  // Don't prune during a refresh
        const currentTime = new Date();

        // Filter out messages older than the message_age_limit
        messages = messages.filter(function(message) {
            const messageTime = new Date(message.time);
            const timeDifference = (currentTime - messageTime) / (1000 * 60 * 60); // time difference in hours

            return timeDifference <= message_age_limit; // Keep only messages within the age limit
        });

        console.log("Messages after removing old ones:", messages);
    }

    // Function to add new messages
    function addMessages(newMessages) {
        if (!newMessages || !Array.isArray(newMessages)) {
            console.error("Invalid data format for messages:", newMessages);
            return;
        }
    
        const currentTime = new Date();
        let newMessagesAdded = 0;  // Track how many new messages were added
    
        newMessages.forEach((newMessage) => {
            const messageTime = new Date(newMessage.time);
            const timeDifference = (currentTime - messageTime) / (1000 * 60 * 60);  // Time difference in hours
    
            // Check for duplicates by id and channel
            const isDuplicate = messages.some(
                (message) => message.id === newMessage.id && message.channel === newMessage.channel
            );
    
            // Add the message only if it's not a duplicate and within the age limit
            if (!isDuplicate && timeDifference <= message_age_limit) {
                messages.push(newMessage);
                newMessagesAdded++;
                console.log(`Added new message ID: ${newMessage.id} from channel: ${newMessage.channel}`);
    
                // Immediately show push messages
                if (newMessage.is_push) {
                    showPushMessage(newMessage);
                }
            } else if (isDuplicate) {
                // Optionally log duplicates only if necessary for debugging
                console.debug(`Duplicate message found, skipping ID: ${newMessage.id} from channel: ${newMessage.channel}`);
            } else if (timeDifference > message_age_limit) {
                console.debug(`Message ID: ${newMessage.id} is too old, skipping.`);
            }
        });
    
        if (newMessagesAdded > 0) {
            removeOldMessages();  // Remove old messages after new ones are added
    
            // Sort messages in descending order by time
            messages.sort((a, b) => new Date(b.time) - new Date(a.time));
    
            // Show the next message if nothing is currently being displayed
            if (!isDisplaying && messages.length > 0) {
                showMessage();  // renders content first
                $("#loading-message").hide();
                $("#messages").show();
            }
    
            console.log(`Number of new messages added: ${newMessagesAdded}`);
        } else {
            console.debug("No new messages added.");
        }
    
        totalMessagesReceived += newMessages.length;
        console.log("Total messages received from server so far:", totalMessagesReceived);
    }

    // Handle successful connection
    socket.on('connect', function () {
        console.log('Successfully connected to the server.');
    
        // Hide the 'lost connection' message when reconnected
        $("#lost-connection").hide();
    
        // Ensure the push indicator is hidden
        $("#push-indicator").hide();
    
        // Set the connection flag to true
        isConnected = true;
    
        // Reset reconnection attempts
        reconnectAttempts = 0;

        // Check if we're not currently displaying messages and there are messages to show
        if (!isDisplaying && messages.length > 0) {
            showMessage();  // Resume showing messages when reconnected
        }
    });

    // Listen for refresh events — fetch new messages without clearing the screen
    socket.on('refresh', function (data) {
        console.log("Received refresh event from server:", data);
        fetchMessagesViaHttp();  // Fetch fresh messages without reloading
    });

    // Handle disconnection
    socket.on('disconnect', function() {
        console.warn('Lost connection to the server.');
        
        // Hide the push indicator (if it's showing)
        $("#push-indicator").hide();
        
        // Show the 'lost connection' message
        $("#lost-connection").text(translations['lost_connection']).show();
    
        isConnected = false;
    
        // Log and attempt to reconnect (remove custom reconnection logic if using built-in)
        console.log("Attempting to reconnect...");
        // If using built-in reconnection, no need to call attemptReconnection
        // If you opted for custom reconnection, ensure the following is appropriate
        // attemptReconnection(); // Remove if using built-in
    });

    // Handle reconnection attempts
    socket.on('reconnect_attempt', function (attempt) {
        console.log(`Reconnection attempt ${attempt}`);
    });

    // Handle reconnection success
    socket.on('reconnect', function () {
        console.log('Reconnected to the server.');
    
        // Hide the 'lost connection' message when reconnected
        $("#lost-connection").hide();
    
        // Ensure 'push-indicator' is hidden unless a push message arrives
        $("#push-indicator").hide();
    
        isConnected = true;
        reconnectAttempts = 0; // Reset reconnection attempts on successful reconnection
    
        if (!isDisplaying && messages.length > 0) {
            showMessage();  // Resume showing messages when reconnected
        }
    });

    // Handle reconnection failure after max attempts
    socket.on('reconnect_failed', function () {
        console.error('Failed to reconnect to the server.');
	$("#lost-connection").text(translations['lost_connection']).show();
        isConnected = false;  // Ensure the flag remains false if reconnection fails
    });

    // Function to display push messages
    function showPushMessage(messageData) {
        $("#push-indicator").text(translations['push_message']).show();
        
        // Optionally, highlight the push message or perform other UI actions
        console.log(`Push message displayed: ID ${messageData.id}`);
    }

    // Listen for initial messages from server
    socket.on('initial_messages', function (data) {
        console.debug("Received initial messages:", data);
        addMessages(data.messages);
        console.log("Number of initial messages received:", data.messages.length);
    });

    // Handle incoming new messages
    socket.on('new_message', function (data) {
        console.debug("Received new message:", data);
        addMessages([data]);  
    });

    // Handle total processed messages (optional)
    socket.on('total_processed', function (data) {
        console.log('Total messages processed by server:', data.count);
    });

    // Function to stop displaying messages when disconnected
    function stopMessageDisplay() {
        clearTimeout(timeoutId);
        clearTimeout(backupTimeoutId);
        isDisplaying = false;
    }

    // Function to extract and display message content
    function extractMessageContent(messageData) {
        console.debug("Extracting message content:", messageData);

        let messageText = messageData.message || "";
        if (typeof messageText !== 'string') {
            console.error("messageText is not a string:", messageText);
            messageText = String(messageText);
        }

        $("#message-media").html(''); 

        const imageRegex = /<img[^>]+src="([^"]+)"[^>]*>/g;
        // Match <video ... src="URL"> or <video ...><source src="URL" ...>
        const videoRegex = /<video[^>]*(?:\s+src="([^"]+)"[^>]*>|>[\s\S]*?<source[^>]+src="([^"]+)"[^>]*>)[\s\S]*?<\/video>/gi;

        // Extract and clean message text — remove all image and video tags
        let cleanedMessage = messageText.replace(imageRegex, '').replace(videoRegex, '');

        let imageMatch;
        while ((imageMatch = imageRegex.exec(messageText)) !== null) {
            if (imageMatch[1]) {
                const imageUrl = imageMatch[1];
                console.debug("Adding image to message media:", imageUrl);
                // Tap to enlarge in a fullscreen overlay
                $("#message-media").append(`<img src="${imageUrl}" alt="Photo" class="message-image" onclick="openImageOverlay(this.src)">`);
            }
        }

        let videoMatch;
        // Reset regex state for exec loop
        videoRegex.lastIndex = 0;
        while ((videoMatch = videoRegex.exec(messageText)) !== null) {
            // Group 1 = src on <video> itself, Group 2 = src on <source> child
            var videoUrl = videoMatch[1] || videoMatch[2];
            if (videoUrl) {
                console.debug("Adding video to message media:", videoUrl);
                $("#message-media").append(`
                    <video controls playsinline preload="auto" class="message-video"
                           onclick="this.paused ? this.play() : this.pause()">
                        <source src="${videoUrl}" type="video/mp4">
                        Your browser does not support the video tag.
                    </video>
                `);
            }
        }

        return cleanedMessage;
    }

    // **Consolidated `showMessage` Function**
    function showMessage() {
        if (messages.length === 0) {
            console.warn("No messages to display yet.");
            isDisplaying = false;
            return;
        }

        // Reset currentIndex if it exceeds the number of messages
        if (currentIndex >= messages.length) {
            currentIndex = 0;
            console.debug("All messages have been displayed. Restarting loop.");
        }

        var messageData = messages[currentIndex];

        if (messageData) {
            console.debug("Showing message: ", messageData);

            var channelName = messageData.channel;
            var message = extractMessageContent(messageData);

            var messageDateUTC = new Date(messageData.time);
            if (isNaN(messageDateUTC.getTime())) {
                console.error("Invalid message date:", messageData.time);
                return;
            }

            var currentTime = new Date();
            var currentTimeUTC = new Date(currentTime.toISOString());

            var timeDifference = (currentTimeUTC - messageDateUTC) / (1000 * 60 * 60); // difference in hours

            console.debug("Message date UTC:", messageDateUTC);
            console.debug("Current time UTC:", currentTimeUTC);

            var options = {
                timeZone: 'Asia/Jerusalem',
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false
            };
            var messageTime = new Intl.DateTimeFormat('he-IL', options).format(messageDateUTC);

            $("#channel-name").text(channelName);
            $("#message-text").html(message);

            if (timeDifference <= message_age_limit) { // Use the correct variable
                $("#message-time").removeClass("old-message").addClass("recent-message");
            } else {
                $("#message-time").removeClass("recent-message").addClass("old-message");
            }

            $("#message-time").text(`${translations['message_time']}: ${messageTime}`);

            if (messageData.is_push) {
                $("#push-indicator").text(translations['push_message']).show();
            } else {
                $("#push-indicator").hide();
            }

            currentIndex = (currentIndex + 1) % messages.length;  // Loop through messages continuously

            isDisplaying = true;

            // Update message counter
            $("#message-counter").text((currentIndex === 0 ? messages.length : currentIndex) + " / " + messages.length);

            clearTimeout(timeoutId);
            clearTimeout(backupTimeoutId);

            // Set primary timeout for the next message
            timeoutId = setTimeout(function () {
                console.debug("Timeout for next message reached, moving to next message.");
                isDisplaying = false;
                showMessage();
            }, 5000); // 5 seconds per message

            // Set backup timeout to handle any delays in displaying
            backupTimeoutId = setTimeout(function () {
                if (isDisplaying) {
                    console.debug("Backup timeout reached, forcing next message.");
                    isDisplaying = false;
                    showMessage();
                }
            }, 15000); // 15 seconds backup timeout
        } else {
            console.warn("Invalid message or all messages displayed.");
            isDisplaying = false;
            // Optional: Restart the loop immediately
            showMessage();
        }
    }

    // Navigate to a specific message by direction: -1 = previous, +1 = next
    function navigateMessage(direction) {
        if (messages.length === 0) return;

        // currentIndex already points to the NEXT message to show (was incremented in showMessage)
        // So the currently displayed message is at (currentIndex - 1)
        // For "next": we want to show currentIndex (which showMessage will do)
        // For "prev": we want to go back 2 from currentIndex
        if (direction === -1) {
            currentIndex = (currentIndex - 2 + messages.length) % messages.length;
        }
        // direction === +1: currentIndex already points to next, no adjustment needed

        // Reset timers and show
        clearTimeout(timeoutId);
        clearTimeout(backupTimeoutId);
        isDisplaying = false;
        showMessage();
    }

    // Instagram-style tap navigation: detect touch position on the ENTIRE page
    // Left 35% = previous, Right 35% = next, Center = ignored (allows text selection)
    function handleNavTap(e) {
        if (messages.length === 0) return;
        var target = e.target || e.srcElement;
        var tag = (target.tagName || '').toLowerCase();
        // Skip clicks on interactive elements
        if (tag === 'button' || tag === 'select' || tag === 'input' || tag === 'option' ||
            tag === 'a' || tag === 'video' || tag === 'source' ||
            $(target).closest('button, select, a, video, #image-overlay').length) {
            return;
        }
        var pageWidth = $(window).width();
        var clientX = e.pageX;
        // For touch events, use the touch coordinate
        if (e.originalEvent && e.originalEvent.changedTouches && e.originalEvent.changedTouches.length > 0) {
            clientX = e.originalEvent.changedTouches[0].pageX;
        }
        if (clientX < pageWidth * 0.35) {
            navigateMessage(-1);
        } else if (clientX > pageWidth * 0.65) {
            navigateMessage(1);
        }
    }
    $(document).on("click", handleNavTap);
    // Also listen for touchend for faster response on mobile
    $(document).on("touchend", function(e) {
        // Prevent double-fire (touchend + click)
        if (e.originalEvent && e.originalEvent.changedTouches) {
            e.preventDefault();
            handleNavTap(e);
        }
    });

    // Event listeners for UI interactions
    $("#refreshFeed").on('click', function () {
        // Lazy-load: keep existing messages visible until new ones arrive.
        // Only use HTTP (Socket.IO cross-thread emit is unreliable and causes races).
        var btn = $(this);
        btn.prop('disabled', true).text('⟳ …');
        isRefreshing = true;
        $.getJSON('/api/messages', function (data) {
            if (data.messages && data.messages.length > 0) {
                console.log("Refresh: received " + data.messages.length + " messages");
                if (!isConnected) {
                    isConnected = true;
                    $("#lost-connection").hide();
                }
                addMessages(data.messages);
            }
        }).always(function () {
            isRefreshing = false;
            btn.prop('disabled', false).text(translations['refresh_feed'] || 'Refresh Feed');
        });
    });

    // HTTP fallback for fetching messages (bypasses Socket.IO threading issues)
    function fetchMessagesViaHttp() {
        $.getJSON('/api/messages', function (data) {
            if (data.messages && data.messages.length > 0) {
                console.log("HTTP fallback: received " + data.messages.length + " messages");
                // Server responded — treat as connected even if Socket.IO is down
                if (!isConnected) {
                    isConnected = true;
                    $("#lost-connection").hide();
                }
                addMessages(data.messages);
            }
        }).fail(function () {
            console.warn("HTTP fallback: /api/messages request failed");
        });
    }

    // Poll backend status and messages until messages arrive.
    // Shows a visible status line so the user knows what's happening.
    // Immediate first poll (don't wait 3s)
    fetchMessagesViaHttp();
    var _pollInterval = 3000; // start at 3s
    var _statusTimer = setInterval(function () {
        // Always poll status to show progress
        $.getJSON('/api/status', function (st) {
            var el = $("#backend-status");
            if (messages.length === 0) {
                el.show().text(st.status + " (channels: " + st.channels + ", msgs: " + st.messages + ")");
            } else {
                el.hide();
            }
        }).fail(function() {});

        // Poll messages via HTTP until we have some
        if (messages.length === 0) {
            fetchMessagesViaHttp();
        } else if (_pollInterval < 30000) {
            // Once we have messages, slow down to 30s and keep as a background refresh
            _pollInterval = 30000;
            clearInterval(_statusTimer);
            _statusTimer = setInterval(function() { fetchMessagesViaHttp(); }, 30000);
        }
    }, _pollInterval);

    function changeLanguage(lang) {
        window.location.href = `/set_language/${lang}`;
    }

    // Periodically remove old messages
    setInterval(function() {
        removeOldMessages();
    }, 60000); // Run every minute to clean up old messages
});
