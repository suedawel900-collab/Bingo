// static/script.js - Main game logic for bingo

class BingoGame {
    constructor(userId, gameId) {
        this.userId = userId;
        this.gameId = gameId;
        this.card = [];
        this.markedNumbers = [];
        this.calledNumbers = [];
        this.players = [];
        this.gameActive = true;
        this.winner = null;
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
    }

    // Initialize the game
    async init() {
        this.generateCard();
        this.renderCard();
        this.connectWebSocket();
        this.setupEventListeners();
        await this.joinGame();
    }

    // Generate random bingo card
    generateCard() {
        const columns = [
            { letter: 'B', min: 1, max: 15 },
            { letter: 'I', min: 16, max: 30 },
            { letter: 'N', min: 31, max: 45 },
            { letter: 'G', min: 46, max: 60 },
            { letter: 'O', min: 61, max: 75 }
        ];

        for (let col = 0; col < 5; col++) {
            const column = [];
            const numbers = new Set();

            while (numbers.size < 5) {
                const num = Math.floor(Math.random() * 
                    (columns[col].max - columns[col].min + 1)) + columns[col].min;
                numbers.add(num);
            }

            this.card.push(Array.from(numbers));
        }

        // Free space in center
        this.card[2][2] = 'FREE';
    }

    // Render bingo card
    renderCard() {
        const cardGrid = document.getElementById('bingo-card');
        if (!cardGrid) return;

        cardGrid.innerHTML = '';

        for (let row = 0; row < 5; row++) {
            for (let col = 0; col < 5; col++) {
                const cell = document.createElement('div');
                cell.className = 'card-cell';
                
                const number = this.card[col][row];
                cell.textContent = number;
                cell.dataset.row = row;
                cell.dataset.col = col;
                cell.dataset.number = number;

                if (number === 'FREE') {
                    cell.classList.add('free', 'marked');
                } else if (this.markedNumbers.includes(number)) {
                    cell.classList.add('marked');
                }

                cell.addEventListener('click', () => this.markNumber(number));
                cardGrid.appendChild(cell);
            }
        }
    }

    // Mark number on card
    markNumber(number) {
        if (number === 'FREE' || !this.gameActive || this.winner) return;

        if (!this.markedNumbers.includes(number) && this.calledNumbers.includes(number)) {
            this.markedNumbers.push(number);
            this.renderCard();
            
            // Send to server
            this.sendToServer({
                type: 'mark_number',
                number: number
            });

            // Check for bingo after marking
            if (this.checkBingo()) {
                this.declareBingo();
            }
        }
    }

    // Check if player has bingo
    checkBingo() {
        // Check rows
        for (let row = 0; row < 5; row++) {
            let bingo = true;
            for (let col = 0; col < 5; col++) {
                const num = this.card[col][row];
                if (num !== 'FREE' && !this.markedNumbers.includes(num)) {
                    bingo = false;
                    break;
                }
            }
            if (bingo) return true;
        }

        // Check columns
        for (let col = 0; col < 5; col++) {
            let bingo = true;
            for (let row = 0; row < 5; row++) {
                const num = this.card[col][row];
                if (num !== 'FREE' && !this.markedNumbers.includes(num)) {
                    bingo = false;
                    break;
                }
            }
            if (bingo) return true;
        }

        // Check diagonals
        let diag1 = true;
        let diag2 = true;
        for (let i = 0; i < 5; i++) {
            const num1 = this.card[i][i];
            const num2 = this.card[4-i][i];

            if (num1 !== 'FREE' && !this.markedNumbers.includes(num1)) diag1 = false;
            if (num2 !== 'FREE' && !this.markedNumbers.includes(num2)) diag2 = false;
        }

        return diag1 || diag2;
    }

    // Declare bingo
    async declareBingo() {
        if (!this.gameActive || this.winner) return;

        this.gameActive = false;
        this.showStatus('🎉 BINGO! Checking with server...', 'info');

        try {
            const response = await fetch('/api/check-bingo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: this.userId,
                    game_id: this.gameId,
                    card: this.card,
                    marked: this.markedNumbers
                })
            });

            const result = await response.json();

            if (result.valid) {
                this.showStatus('🎉 CONGRATULATIONS! You won! 🎉', 'success');
                document.getElementById('bingo-button').disabled = true;
            } else {
                this.showStatus('❌ Not a valid BINGO!', 'error');
                this.gameActive = true;
            }

        } catch (error) {
            console.error('Error checking bingo:', error);
            this.showStatus('Error checking BINGO', 'error');
            this.gameActive = true;
        }
    }

    // Connect to WebSocket
    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/${this.gameId}/${this.userId}`;
        
        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.reconnectAttempts = 0;
            this.showStatus('Connected to game!', 'success');
        };

        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.handleWebSocketMessage(data);
        };

        this.ws.onclose = () => {
            console.log('WebSocket disconnected');
            this.reconnect();
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.showStatus('Connection error', 'error');
        };
    }

    // Reconnect WebSocket
    reconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            this.showStatus(`Reconnecting... (${this.reconnectAttempts}/${this.maxReconnectAttempts})`, 'info');
            
            setTimeout(() => {
                this.connectWebSocket();
            }, 2000 * this.reconnectAttempts);
        } else {
            this.showStatus('Unable to connect to server', 'error');
        }
    }

    // Handle WebSocket messages
    handleWebSocketMessage(data) {
        switch (data.type) {
            case 'number_called':
                this.onNumberCalled(data);
                break;
            case 'player_joined':
                this.onPlayerJoined(data);
                break;
            case 'game_won':
                this.onGameWon(data);
                break;
            case 'game_over':
                this.onGameOver(data);
                break;
            case 'number_marked':
                // Number marked successfully
                break;
            case 'bingo_result':
                // Bingo check result
                break;
        }
    }

    // Handle number called
    onNumberCalled(data) {
        this.calledNumbers.push(data.number);
        
        // Update UI
        document.getElementById('last-number').textContent = `🎲 ${data.number}`;
        document.getElementById('called-count').textContent = this.calledNumbers.length;

        // Play sound if number is on player's card
        const isOnCard = this.isNumberOnCard(data.number);
        if (isOnCard) {
            this.showStatus(`✅ Number ${data.number} is on your card!`, 'success');
            this.playSound('match');
        } else {
            this.playSound('call');
        }
    }

    // Handle player joined
    onPlayerJoined(data) {
        this.players = data.players;
        this.updatePlayersList();
        document.getElementById('player-count').textContent = this.players.length;
        
        if (data.player_id !== this.userId) {
            this.showStatus(`👋 Player joined the game!`, 'info');
            this.playSound('join');
        }
    }

    // Handle game won
    onGameWon(data) {
        this.gameActive = false;
        this.winner = data.winner_id;
        
        document.getElementById('bingo-button').disabled = true;

        if (data.winner_id == this.userId) {
            this.showStatus('🏆 YOU WON! 🏆', 'success');
            this.playSound('win');
        } else {
            this.showStatus(`👑 ${data.winner_name} won!`, 'info');
            this.playSound('gameover');
        }

        // Update prize pool
        document.getElementById('prize-pool').textContent = data.prize;
        
        // Update players list to show winner
        this.updatePlayersList();
    }

    // Handle game over
    onGameOver(data) {
        this.gameActive = false;
        this.showStatus(data.message, 'info');
        document.getElementById('bingo-button').disabled = true;
        this.playSound('gameover');
    }

    // Check if number is on player's card
    isNumberOnCard(number) {
        for (let col = 0; col < 5; col++) {
            for (let row = 0; row < 5; row++) {
                if (this.card[col][row] === number) {
                    return true;
                }
            }
        }
        return false;
    }

    // Join game
    async joinGame() {
        try {
            const response = await fetch('/api/join-game', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: this.userId,
                    game_id: this.gameId,
                    card: this.card
                })
            });

            const result = await response.json();

            if (result.success) {
                this.showStatus('Joined game! Waiting for numbers...', 'success');
                // Update balance display if needed
                if (result.new_balance !== undefined) {
                    this.updateBalance(result.new_balance);
                }
            } else {
                this.showStatus('Failed to join game: ' + (result.error || 'Unknown error'), 'error');
                setTimeout(() => {
                    window.location.href = '/';
                }, 3000);
            }

        } catch (error) {
            console.error('Error joining game:', error);
            this.showStatus('Error joining game', 'error');
        }
    }

    // Send message to server via WebSocket
    sendToServer(data) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(data));
        }
    }

    // Update players list UI
    updatePlayersList() {
        const container = document.getElementById('players-list');
        if (!container) return;

        container.innerHTML = '';

        this.players.forEach(player => {
            const badge = document.createElement('span');
            badge.className = 'player-badge';
            badge.textContent = player.name;

            if (player.is_winner || player.id === this.winner) {
                badge.classList.add('winner');
                badge.textContent += ' 👑';
            }

            container.appendChild(badge);
        });
    }

    // Update balance display
    updateBalance(newBalance) {
        const balanceElement = document.getElementById('user-balance');
        if (balanceElement) {
            balanceElement.textContent = `$${(newBalance/100).toFixed(2)}`;
        }
    }

    // Show status message
    showStatus(message, type) {
        const statusDiv = document.getElementById('status-message');
        if (!statusDiv) return;

        statusDiv.textContent = message;
        statusDiv.className = `status-message ${type}`;
        
        // Auto hide after 3 seconds for non-error messages
        if (type !== 'error') {
            setTimeout(() => {
                statusDiv.style.display = 'none';
            }, 3000);
        }
    }

    // Play sound effects
    playSound(type) {
        // Optional: Add sound effects
        // You can implement this with Web Audio API or simple beeps
        console.log(`Playing sound: ${type}`);
    }

    // Setup event listeners
    setupEventListeners() {
        // Bingo button
        const bingoButton = document.getElementById('bingo-button');
        if (bingoButton) {
            bingoButton.addEventListener('click', () => this.declareBingo());
        }

        // Handle page visibility
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                // Page hidden
            } else {
                // Page visible again, check connection
                if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
                    this.reconnect();
                }
            }
        });
    }
}

// Initialize game when page loads
document.addEventListener('DOMContentLoaded', async () => {
    // Get URL parameters
    const urlParams = new URLSearchParams(window.location.search);
    const userId = parseInt(urlParams.get('user_id'));
    const gameId = parseInt(urlParams.get('game_id'));

    if (!userId) {
        document.body.innerHTML = '<h1>Error: User ID required</h1>';
        return;
    }

    // Create and initialize game
    const game = new BingoGame(userId, gameId || 1);
    window.game = game; // Make available globally for debugging
    await game.init();
});

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = BingoGame;
}