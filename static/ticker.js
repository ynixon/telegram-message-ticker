$(document).ready(function () {
    // 1. Check if Socket.IO is loaded
    if (typeof io === 'undefined') {
        console.error("Socket.IO is not loaded. Please check the script tag and integrity attribute.");
        alert("Failed to load Socket.IO. Please check the browser console for more details.");
        return;
    }

    var socket = io();
    var currentIndex = 0;
    var messages = [];
    var timeoutId = null;
    var backupTimeoutId = null;
    var isDisplaying = false;
    var totalMessagesReceived = 0;  // Added variable to track total messages received

    // Handle both initial and new messages in a unified way
    function addMessages(newMessages) {
        if (!newMessages || !Array.isArray(newMessages)) {
            console.error("Invalid data format for messages:", newMessages);
            return;
        }

        // Add new messages and keep only unique ones
        newMessages.forEach((newMessage) => {
            // Optional: Check for duplicates based on 'id'
            if (!messages.some((message) => message.id === newMessage.id)) {
                messages.push(newMessage);
            }
        });

        // Update the total messages received counter
        totalMessagesReceived += newMessages.length;

        // Log the counts
        console.log("Number of new messages received:", newMessages.length);
        console.log("Total messages received from server so far:", totalMessagesReceived);

        // Sort messages by time descending
        messages = messages.sort((a, b) => new Date(b.time) - new Date(a.time));
        console.log("Updated messages array:", messages);

        console.debug("Messages updated, new message list:", messages);
        console.debug("isDisplaying before showing the next message:", isDisplaying);

        // Start showing messages if not currently displaying
        if (!isDisplaying) {
            showMessage();  // Do not reset currentIndex here
        }
    }

    // Listen for initial messages from server
    socket.on('initial_messages', function (data) {
        console.debug("Received initial messages:", data);
        addMessages(data.messages);
        console.log("Number of initial messages received:", data.messages.length);
    });

    // In the socket.on('new_message') handler
    socket.on('new_message', function (data) {
        console.debug("Received new message:", data);
        addMessages([data]);  // Correct: Passing the entire message object
    });

    // Optionally handle the total_processed event if the server sends it
    socket.on('total_processed', function (data) {
        console.log('Total messages processed by server:', data.count);
    });

    // Function to extract and display message content
    function extractMessageContent(messageData) {
        console.debug("Extracting message content:", messageData);

        let messageText = messageData.message || "";
        if (typeof messageText !== 'string') {
            console.error("messageText is not a string:", messageText);
            messageText = String(messageText); // Convert to string
        }

        $("#message-media").html(''); // Clear previous media

        const imageRegex = /<img[^>]+src="([^"]+)"[^>]*>/g;
        const videoRegex = /<video[^>]+src="([^"]+)"[^>]*>/g;

        // Remove media tags from the message text, since media should be handled separately
        let cleanedMessage = messageText.replace(imageRegex, '').replace(videoRegex, '');

        // Handle images
        let imageMatch;
        while ((imageMatch = imageRegex.exec(messageText)) !== null) {
            if (imageMatch[1]) {
                const imageUrl = imageMatch[1];
                console.debug("Adding image to message media:", imageUrl);
                $("#message-media").append(`<img src="${imageUrl}" alt="Photo" class="message-image">`);
            }
        }

        // Handle videos
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

        return cleanedMessage; // Return the cleaned message text without media tags
    }

    // Fetch messages continuously, but keep displaying existing ones
    function showMessage() {
        if (messages.length === 0) {
            console.warn("No messages to display yet.");
            isDisplaying = false;
            return;
        }

        if (currentIndex >= messages.length) {
            currentIndex = 0; // Reset to the beginning once we reach the end
        }

        var messageData = messages[currentIndex];
        if (messageData) {
            isDisplaying = true;
            console.debug("Showing message: ", messageData);

            var channelName = messageData.channel;
            var message = extractMessageContent(messageData);

            // Parse the message time as UTC, and get the current time in UTC
            var messageDateUTC = new Date(messageData.time);
            if (isNaN(messageDateUTC.getTime())) {
                console.error("Invalid message date:", messageData.time);
                return;
            }            
            var currentTime = new Date(); // Current local time
            var currentTimeUTC = new Date(currentTime.toISOString()); // Convert to UTC for consistency

            // Calculate time difference in minutes
            var timeDifference = (currentTimeUTC - messageDateUTC) / (1000 * 60);

            console.debug("Message date UTC:", messageDateUTC);
            console.debug("Current time UTC:", currentTimeUTC);
            if (isNaN(messageDateUTC)) {
                console.error("Invalid message date:", messageData.time);
                return;
            }
            console.debug("Time difference in minutes:", timeDifference);

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
    
            // Change text color based on message age
            if (timeDifference <= 60) {  // Less than or equal to 1 hour
                $("#message-time").removeClass("old-message").addClass("recent-message");
            } else {
                $("#message-time").removeClass("recent-message").addClass("old-message");
            }
    
            $("#message-time").text("נשלח ב: " + messageTime);
    
            // Handle the push indicator
            if (messageData.is_push) {
                $("#push-indicator").text("הודעת פוש התקבלה!").show(); // Show the push indicator
            } else {
                $("#push-indicator").hide(); // Hide the push indicator for non-push messages
            }

            // Increment currentIndex for the next message
            console.debug("Current index before increment:", currentIndex);
            currentIndex = (currentIndex + 1) % messages.length;
            console.debug("Current index after increment:", currentIndex);

            // Clear previous timeouts
            clearTimeout(timeoutId);
            clearTimeout(backupTimeoutId);

            // Set up timeout for the next message
            timeoutId = setTimeout(function () {
                console.debug("Timeout for next message reached, moving to next message.");
                isDisplaying = false;
                showMessage();
            }, 5000);

            // Additional backup timeout
            backupTimeoutId = setTimeout(function () {
                if (isDisplaying) {
                    console.debug("Backup timeout reached, forcing next message.");
                    isDisplaying = false;
                    showMessage();
                }
            }, 15000);
        } else {
            isDisplaying = false;
        }
    }

    // Refresh Feed Button Handler
    $("#refreshFeed").on('click', function () {
        location.reload();  // Reload the page to fetch new messages
    });
});
