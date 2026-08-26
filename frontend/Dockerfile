FROM node:18-alpine

# Set working directory
WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install

# Copy application code
COPY . .

# Expose port
EXPOSE 3000

# Default command (development mode)
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
