"""Run the web application."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("src.web.app:app", host="0.0.0.0", port=8085, reload=True)
