from quart import Quart
from quart_cors import cors
from quart_schema import QuartSchema
from app.app_modules.base.config import ALLOWED_ORIGINS
from app.app_modules.base.database import init_tortoise, close_tortoise
from app.app_modules.base.redis_client import redis_manager

# Blueprints
from app.gateway.resources.auth import auth_bp
from app.gateway.resources.campaigns import campaigns_bp
from app.gateway.resources.maps import maps_bp
from app.gateway.resources.characters import characters_bp
from app.gateway.resources.websocket import ws_bp

def create_app() -> Quart:
    app = Quart(__name__)
    
    # Configure CORS
    app = cors(
        app,
        allow_origin=ALLOWED_ORIGINS,
        allow_headers=["Content-Type", "Authorization"],
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    )
    
    # Configure Swagger/OpenAPI Schema
    QuartSchema(app, info={"title": "VTT API", "version": "1.0.0"})
    
    # Root status endpoint
    @app.route("/")
    async def index():
        return {"status": "VTT Backend is running!"}
        
    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(campaigns_bp)
    app.register_blueprint(maps_bp)
    app.register_blueprint(characters_bp)
    app.register_blueprint(ws_bp)
    
    # Lifecycle Hooks
    @app.before_serving
    async def startup():
        # Init Redis connection
        await redis_manager.init()
        # Init Tortoise ORM connection
        await init_tortoise()
        
    @app.after_serving
    async def shutdown():
        # Close Redis connection
        await redis_manager.close()
        # Close Tortoise ORM connection
        await close_tortoise()
        
    return app
