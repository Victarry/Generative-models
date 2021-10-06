from pathlib import Path

import hydra
import pytorch_lightning as pl
import torch
import torch.nn.functional as F
import torchvision
from src.utils import utils
from .base import BaseModel


class WGAN(BaseModel):
    def __init__(
        self,
        channels,
        width,
        height,
        netG,
        netD,
        latent_dim=100,
        n_critic=5,
        clip_weight=0.01,
        lrG: float = 2e-4,
        lrD: float = 2e-4,
        b1: float = 0.5,
        b2: float = 0.999,
        input_normalize=True,
        optim="adam",
        **kwargs,
    ):
        super().__init__()
        self.save_hyperparameters()

        # networks
        self.generator = hydra.utils.instantiate(
            netG, input_channel=latent_dim, output_channel=channels
        )
        self.discriminator = hydra.utils.instantiate(
            netD, input_channel=channels, output_channel=1
        )

    def forward(self, z):
        output = self.generator(z)
        output = output.reshape(
            z.shape[0], self.hparams.channels, self.hparams.height, self.hparams.width
        )
        return output

    def on_train_epoch_end(self):
        result_path = Path("results")
        result_path.mkdir(parents=True, exist_ok=True)
        if hasattr(self, "z"):
            z = self.z
        else:
            self.z = z = torch.randn(64, self.hparams.latent_dim).to(self.device)
        imgs = self.generator(z)
        grid = self.get_grid_images(imgs)
        torchvision.utils.save_image(grid, result_path / f"{self.current_epoch}.jpg")

    def training_step(self, batch, batch_idx, optimizer_idx):
        imgs, _ = batch  # (N, C, H, W)
        if self.hparams.input_normalize:
            imgs = imgs * 2 - 1

        # sample noise
        z = torch.randn(imgs.shape[0], self.hparams.latent_dim)  # (N, latent_dim)
        z = z.type_as(imgs)

        # clip discriminator weight for 1-Lipschitz constraint, clip的位置影响很大...
        for p in self.discriminator.parameters():
            p.data.clamp_(-self.hparams.clip_weight, self.hparams.clip_weight)
        # train generator, pytorch_lightning will automatically set discriminator requires_gard as False
        if optimizer_idx == 0:

            # generate images
            generated_imgs = self(z)

            # log sampled images
            self.log_images(generated_imgs, "generated_images")

            # adversarial loss is binary cross-entropy
            g_loss = -torch.mean(self.discriminator(generated_imgs))
            self.log("train_loss/g_loss", g_loss, prog_bar=True)
            return g_loss

        # train discriminator
        if optimizer_idx == 1:
            # real loss
            real_loss = -self.discriminator(imgs).mean()

            # fake loss
            fake_loss = self.discriminator(self(z).detach()).mean()

            # discriminator loss is the average of these
            d_loss = real_loss + fake_loss
            self.log("train_loss/d_loss", d_loss)
            self.log("train_log/real_logit", -real_loss)
            self.log("train_log/fake_logit", fake_loss)

            return d_loss

    def configure_optimizers(self):
        lrG = self.hparams.lrG
        lrD = self.hparams.lrD
        b1 = self.hparams.b1
        b2 = self.hparams.b2

        if self.hparams.optim == "adam":
            opt_g = torch.optim.Adam(
                self.generator.parameters(), lr=lrG, betas=(b1, b2)
            )
            opt_d = torch.optim.Adam(
                self.discriminator.parameters(), lr=lrD, betas=(b1, b2)
            )
        elif self.hparams.optim == "sgd":
            opt_g = torch.optim.SGD(self.generator.parameters(), lr=lrG)
            opt_d = torch.optim.SGD(self.discriminator.parameters(), lr=lrD)
        elif self.hparams.optim == "rmsprop":
            opt_g = torch.optim.RMSprop(self.generator.parameters(), lr=lrG)
            opt_d = torch.optim.RMSprop(self.discriminator.parameters(), lr=lrD)
        return [
            {"optimizer": opt_g, "frequency": 1},
            {"optimizer": opt_d, "frequency": self.hparams.n_critic},
        ]