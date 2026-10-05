def create_app(config_class=None):
    from flask import Flask

    if config_class is None:
        from .config import Config

        config_class = Config

    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(config_class)

    from .routes import bp

    app.register_blueprint(bp)

    return app
