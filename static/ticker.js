$(document).ready(function () {
    // Corrected variable name for consistency
    var message_age_limit_in_hours = 2; // in hours
    console.log("Message age limit set to:", message_age_limit_in_hours, "hours");

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

    // Set message_age_limit to 2 hours to match the server configuration
    var message_age_limit = message_age_limit_in_hours; // in hours

    // Function to remove old messages
    function removeOldMessages() {
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
            if (!isDisplaying && isConnected && messages.length > 0) {
                $("#loading-message").hide();
                $("#messages").show();
                showMessage();  
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

    // Listen for refresh events and reload the page when triggered
    socket.on('refresh', function (data) {
        console.log("Received refresh event from server:", data);
        location.reload();  // Reload the page on refresh event
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
        if (!hasInitialMessagesLoaded) {
            console.debug("Received initial messages:", data);
            addMessages(data.messages);
            console.log("Number of initial messages received:", data.messages.length);
            hasInitialMessagesLoaded = true; // Mark initial messages as loaded
        } else {
            console.warn("Initial messages already processed. Skipping...");
        }
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
        const videoRegex = /<video[^>]+src="([^"]+)"[^>]*>/g;

        // Extract and clean message text
        let cleanedMessage = messageText.replace(imageRegex, '').replace(videoRegex, '');

        let imageMatch;
        while ((imageMatch = imageRegex.exec(messageText)) !== null) {
            if (imageMatch[1]) {
                const imageUrl = imageMatch[1];
                console.debug("Adding image to message media:", imageUrl);
                $("#message-media").append(`<img src="${imageUrl}" alt="Photo" class="message-image">`);
            }
        }

        let videoMatch;
        while ((videoMatch = videoRegex.exec(messageText)) !== null) {
            if (videoMatch[1]) {
                const videoUrl = videoMatch[1];
                console.debug("Adding video to message media:", videoUrl);
                $("#message-media").append(`
                    <video controls autoplay class="message-video">
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
        if (!isConnected) {
            console.warn("Cannot display messages while disconnected.");
            return;  // Don't show messages while disconnected
        }

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

    // Event listeners for UI interactions
    $("#refreshFeed").on('click', function () {
        location.reload();
    });

    function changeLanguage(lang) {
        window.location.href = `/set_language/${lang}`;
    }

    // Periodically remove old messages
    setInterval(function() {
        removeOldMessages();
    }, 60000); // Run every minute to clean up old messages
});
