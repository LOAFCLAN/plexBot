import discord

from loguru import logger as logging


class ReviewModal(discord.ui.Modal):
    review_value = discord.ui.TextInput(label="Score", style=discord.TextStyle.short, max_length=3, required=False)

    def __init__(self, media_id, *, timeout=None):
        super().__init__(title="Media Review", timeout=timeout)
        self.media_id = media_id

    async def on_submit(self, interaction: discord.Interaction):  # pylint: disable=arguments-differ
        """Handles when a modal is submitted"""
        review = self.review_value.value
        table = interaction.client.database.get_table("plex_afs_ratings")
        if not review:
            # Used to indicate if the user wants to delete their review
            table.delete(media_id=self.media_id, user_id=interaction.user.id)
            await interaction.response.send_message("Review deleted", ephemeral=True)
            return

        if not review.isdigit():
            await interaction.response.send_message("Score must be a number", ephemeral=True)
            return
        review = int(review)
        if review < 0 or review > 100:
            await interaction.response.send_message("Score must be between 0 and 100", ephemeral=True)
            return
        logging.info(f"Review: {review}")

        row = table.get_row(media_id=self.media_id, user_id=interaction.user.id)
        if row:
            row.set(rating=review)
            await interaction.response.send_message("Review updated", ephemeral=True)
        else:
            table.add(media_id=self.media_id, user_id=interaction.user.id, rating=review)
            await interaction.response.send_message("Review added", ephemeral=True)
