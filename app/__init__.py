from flask import Flask
from config import Config

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    from .views.main import bp as main_bp
    from .views.api import bp as api_bp
    from .views.jobs import bp as jobs_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(jobs_bp, url_prefix="/api/jobs")

    return app